import asyncio
import binascii
import datetime
import enum
import logging
import os
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Type, TypeVar

import pylibsrtp
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from OpenSSL import SSL, crypto
from pyee.asyncio import AsyncIOEventEmitter
from pylibsrtp import Policy, Session

from . import clock, rtp
from .rtcicetransport import RTCIceTransport
from .rtcrtpparameters import RTCRtpReceiveParameters, RTCRtpSendParameters
from .rtp import (
    AnyRtcpPacket,
    RtcpByePacket,
    RtcpPacket,
    RtcpPsfbPacket,
    RtcpRrPacket,
    RtcpRtpfbPacket,
    RtcpSrPacket,
    RtpPacket,
    is_rtcp,
)
from .stats import RTCStatsReport, RTCTransportStats

CERTIFICATE_T = TypeVar("CERTIFICATE_T", bound="RTCCertificate")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SRTPProtectionProfile:
    libsrtp_profile: int
    openssl_profile: bytes
    key_length: int
    salt_length: int

    def get_key_and_salt(self, src, idx: int) -> bytes:
        key_start = idx * self.key_length
        salt_start = 2 * self.key_length + idx * self.salt_length
        return (
            src[key_start : key_start + self.key_length]
            + src[salt_start : salt_start + self.salt_length]
        )


SRTP_AEAD_AES_256_GCM = SRTPProtectionProfile(
    libsrtp_profile=Policy.SRTP_PROFILE_AEAD_AES_256_GCM,
    openssl_profile=b"SRTP_AEAD_AES_256_GCM",
    key_length=32,
    salt_length=12,
)
SRTP_AEAD_AES_128_GCM = SRTPProtectionProfile(
    libsrtp_profile=Policy.SRTP_PROFILE_AEAD_AES_128_GCM,
    openssl_profile=b"SRTP_AEAD_AES_128_GCM",
    key_length=16,
    salt_length=12,
)
SRTP_AES128_CM_SHA1_80 = SRTPProtectionProfile(
    libsrtp_profile=Policy.SRTP_PROFILE_AES128_CM_SHA1_80,
    openssl_profile=b"SRTP_AES128_CM_SHA1_80",
    key_length=16,
    salt_length=14,
)

# AES-GCM may not be available depending on how libsrtp2 was built.
SRTP_PROFILES: List[SRTPProtectionProfile] = []
for srtp_profile in [
    SRTP_AEAD_AES_256_GCM,
    SRTP_AEAD_AES_128_GCM,
    SRTP_AES128_CM_SHA1_80,
]:
    try:
        Policy(srtp_profile=srtp_profile.libsrtp_profile)
    except pylibsrtp.Error:  # pragma: no cover
        pass
    else:
        SRTP_PROFILES.append(srtp_profile)


def certificate_digest(x509: crypto.X509) -> str:
    return x509.digest("SHA256").decode("ascii")


def generate_certificate(key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    name = x509.Name(
        [
            x509.NameAttribute(
                x509.NameOID.COMMON_NAME,
                binascii.hexlify(os.urandom(16)).decode("ascii"),
            )
        ]
    )
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
    )
    return builder.sign(key, hashes.SHA256(), default_backend())


class State(enum.Enum):
    NEW = 0
    CONNECTING = 1
    CONNECTED = 2
    CLOSED = 3
    FAILED = 4


@dataclass
class RTCDtlsFingerprint:
    """
    The :class:`RTCDtlsFingerprint` dictionary includes the hash function
    algorithm and certificate fingerprint.
    """

    algorithm: str
    "The hash function name, for instance `'sha-256'`."

    value: str
    "The fingerprint value."


class RTCCertificate:
    """
    The :class:`RTCCertificate` interface enables the certificates used by an
    :class:`RTCDtlsTransport`.

    To generate a certificate and the corresponding private key use
    :func:`generateCertificate`.
    """

    def __init__(self, key: crypto.PKey, cert: crypto.X509) -> None:
        self._key = key
        self._cert = cert

    @property
    def expires(self) -> datetime.datetime:
        """
        The date and time after which the certificate will be considered invalid.
        """
        return self._cert.to_cryptography().not_valid_after_utc

    def getFingerprints(self) -> List[RTCDtlsFingerprint]:
        """
        Returns the list of certificate fingerprints, one of which is computed
        with the digest algorithm used in the certificate signature.
        """
        return [
            RTCDtlsFingerprint(
                algorithm="sha-256",
                value=certificate_digest(self._cert),
            )
        ]

    @classmethod
    def generateCertificate(cls: Type[CERTIFICATE_T]) -> CERTIFICATE_T:
        """
        Create and return an X.509 certificate and corresponding private key.

        :rtype: RTCCertificate
        """
        key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        cert = generate_certificate(key)
        return cls(
            key=crypto.PKey.from_cryptography_key(key),
            cert=crypto.X509.from_cryptography(cert),
        )

    def _create_ssl_context(
        self, srtp_profiles: List[SRTPProtectionProfile]
    ) -> SSL.Context:
        ctx = SSL.Context(SSL.DTLS_METHOD)
        ctx.set_verify(
            SSL.VERIFY_PEER | SSL.VERIFY_FAIL_IF_NO_PEER_CERT, lambda *args: 1
        )
        ctx.use_certificate(self._cert)
        ctx.use_privatekey(self._key)
        ctx.set_cipher_list(
            b"ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-SHA:ECDHE-ECDSA-AES256-SHA"
        )
        ctx.set_tlsext_use_srtp(b":".join(x.openssl_profile for x in srtp_profiles))

        return ctx


@dataclass
class RTCDtlsParameters:
    """
    The :class:`RTCDtlsParameters` dictionary includes information relating to
    DTLS configuration.
    """

    fingerprints: List[RTCDtlsFingerprint] = field(default_factory=list)
    "List of :class:`RTCDtlsFingerprint`, one fingerprint for each certificate."

    role: str = "auto"
    "The DTLS role, with a default of auto."


class RtpRouter:
    """
    Router to associate RTP/RTCP packets with streams.

    https://tools.ietf.org/html/draft-ietf-mmusic-sdp-bundle-negotiation-53
    """

    def __init__(self) -> None:
        self.receivers: Set = set()
        self.senders: Dict[int, Any] = {}
        self.mid_table: Dict[str, Any] = {}
        self.ssrc_table: Dict[int, Any] = {}
        self.payload_type_table: Dict[int, Set] = {}

    def register_receiver(
        self,
        receiver,
        ssrcs: List[int],
        payload_types: List[int],
        mid: Optional[str] = None,
    ):
        self.receivers.add(receiver)
        if mid is not None:
            self.mid_table[mid] = receiver
        for ssrc in ssrcs:
            self.ssrc_table[ssrc] = receiver
        for payload_type in payload_types:
            if payload_type not in self.payload_type_table:
                self.payload_type_table[payload_type] = set()
            self.payload_type_table[payload_type].add(receiver)

    def register_sender(self, sender, ssrc: int) -> None:
        self.senders[ssrc] = sender

    def route_rtcp(self, packet: AnyRtcpPacket) -> Set:
        recipients = set()

        def add_recipient(recipient) -> None:
            if recipient is not None:
                recipients.add(recipient)

        # route to RTP receiver
        if isinstance(packet, RtcpSrPacket):
            add_recipient(self.ssrc_table.get(packet.ssrc))
        elif isinstance(packet, RtcpByePacket):
            for source in packet.sources:
                add_recipient(self.ssrc_table.get(source))

        # route to RTP sender
        if isinstance(packet, (RtcpRrPacket, RtcpSrPacket)):
            for report in packet.reports:
                add_recipient(self.senders.get(report.ssrc))
        elif isinstance(packet, (RtcpPsfbPacket, RtcpRtpfbPacket)):
            add_recipient(self.senders.get(packet.media_ssrc))

            # for REMB packets, media_ssrc is always 0, we need to look into the FCI
            if isinstance(packet, RtcpPsfbPacket) and packet.fmt == rtp.RTCP_PSFB_APP:
                try:
                    for ssrc in rtp.unpack_remb_fci(packet.fci)[1]:
                        add_recipient(self.senders.get(ssrc))
                except ValueError:
                    pass

        return recipients

    def route_rtp(self, packet: RtpPacket) -> Optional[Any]:
        ssrc_receiver = self.ssrc_table.get(packet.ssrc)
        pt_receivers = self.payload_type_table.get(packet.payload_type, set())

        # the SSRC and payload type are known and match
        if ssrc_receiver is not None and ssrc_receiver in pt_receivers:
            return ssrc_receiver

        # the SSRC is unknown but the payload type matches, update the SSRC table
        if ssrc_receiver is None and len(pt_receivers) == 1:
            pt_receiver = list(pt_receivers)[0]
            self.ssrc_table[packet.ssrc] = pt_receiver
            return pt_receiver

        # discard the packet
        return None

    def unregister_receiver(self, receiver) -> None:
        self.receivers.discard(receiver)
        self.__discard(self.mid_table, receiver)
        self.__discard(self.ssrc_table, receiver)
        for pt, receivers in self.payload_type_table.items():
            receivers.discard(receiver)

    def unregister_sender(self, sender) -> None:
        self.__discard(self.senders, sender)

    def __discard(self, d: Dict, value: Any) -> None:
        for k, v in list(d.items()):
            if v == value:
                d.pop(k)


class RTCDtlsTransport(AsyncIOEventEmitter):
    """
    The :class:`RTCDtlsTransport` object includes information relating to
    Datagram Transport Layer Security (DTLS) transport.

    :param transport: An :class:`RTCIceTransport`.
    :param certificates: A list of :class:`RTCCertificate` (only one is allowed
        currently).
    """

    def __init__(
        self, transport: RTCIceTransport, certificates: List[RTCCertificate]
    ) -> None:
        assert len(certificates) == 1
        certificate = certificates[0]

        super().__init__()
        self.encrypted = False
        self._data_receiver = None
        self._role = "auto"
        self._rtp_header_extensions_map = rtp.HeaderExtensionsMap()
        self._rtp_router = RtpRouter()
        self._state = State.NEW
        self._stats_id = "transport_" + str(id(self))
        self._task: Optional[asyncio.Future[None]] = None
        self._transport = transport

        # counters
        self.__rx_bytes = 0
        self.__rx_packets = 0
        self.__tx_bytes = 0
        self.__tx_packets = 0

        # SRTP
        self._rx_srtp: Session = None
        self._tx_srtp: Session = None

        # SSL
        self._srtp_profiles = SRTP_PROFILES
        self._ssl: Optional[SSL.Connection] = None
        self.__local_certificate = certificate

    @property
    def state(self) -> str:
        """
        The current state of the DTLS transport.

        One of `'new'`, `'connecting'`, `'connected'`, `'closed'` or `'failed'`.
        """
        return str(self._state)[6:].lower()

    @property
    def transport(self):
        """
        The associated :class:`RTCIceTransport` instance.
        """
        return self._transport

    def getLocalParameters(self) -> RTCDtlsParameters:
        """
        Get the local parameters of the DTLS transport.

        :rtype: :class:`RTCDtlsParameters`
        """
        return RTCDtlsParameters(
            fingerprints=self.__local_certificate.getFingerprints()
        )

    async def start(self, remoteParameters: RTCDtlsParameters) -> None:
        """
        Start DTLS transport negotiation with the parameters of the remote
        DTLS transport.

        :param remoteParameters: An :class:`RTCDtlsParameters`.
        """
        assert self._state == State.NEW
        assert len(remoteParameters.fingerprints)

        # For WebRTC, the DTLS role is explicitly determined as part of the
        # offer / answer exchange.
        #
        # For ORTC however, we determine the DTLS role based on the ICE role.
        if self._role == "auto":
            if self.transport.role == "controlling":
                self._set_role("server")
            else:
                self._set_role("client")

        # Initialise SSL.
        self._ssl = SSL.Connection(
            self.__local_certificate._create_ssl_context(
                srtp_profiles=self._srtp_profiles
            )
        )
        if self._role == "server":
            self._ssl.set_accept_state()
        else:
            self._ssl.set_connect_state()

        self._set_state(State.CONNECTING)
        try:
            while not self.encrypted:
                try:
                    self._ssl.do_handshake()
                except SSL.WantReadError:
                    await self._write_ssl()
                    await self._recv_next()
                except SSL.Error as exc:
                    self.__log_debug("x DTLS handshake failed (error %s)", exc)
                    self._set_state(State.FAILED)
                    return
                else:
                    self.encrypted = True
        except ConnectionError:
            self.__log_debug("x DTLS handshake failed (connection error)")
            self._set_state(State.FAILED)
            return

        # check remote fingerprint
        x509 = self._ssl.get_peer_certificate()
        remote_fingerprint = certificate_digest(x509)
        fingerprint_is_valid = False
        for f in remoteParameters.fingerprints:
            if (
                f.algorithm.lower() == "sha-256"
                and f.value.lower() == remote_fingerprint.lower()
            ):
                fingerprint_is_valid = True
                break
        if not fingerprint_is_valid:
            self.__log_debug("x DTLS handshake failed (fingerprint mismatch)")
            self._set_state(State.FAILED)
            return

        # generate keying material
        openssl_profile = self._ssl.get_selected_srtp_profile()
        for srtp_profile in self._srtp_profiles:
            if srtp_profile.openssl_profile == openssl_profile:
                self.__log_debug(
                    "x DTLS handshake negotiated %s",
                    srtp_profile.openssl_profile.decode(),
                )
                break
        else:
            self.__log_debug("x DTLS handshake failed (no SRTP profile negotiated)")
            self._set_state(State.FAILED)
            return
        view = self._ssl.export_keying_material(
            b"EXTRACTOR-dtls_srtp",
            2 * (srtp_profile.key_length + srtp_profile.salt_length),
        )
        if self._role == "server":
            srtp_tx_key = srtp_profile.get_key_and_salt(view, 1)
            srtp_rx_key = srtp_profile.get_key_and_salt(view, 0)
        else:
            srtp_tx_key = srtp_profile.get_key_and_salt(view, 0)
            srtp_rx_key = srtp_profile.get_key_and_salt(view, 1)

        rx_policy = Policy(
            key=srtp_rx_key,
            ssrc_type=Policy.SSRC_ANY_INBOUND,
            srtp_profile=srtp_profile.libsrtp_profile,
        )
        rx_policy.allow_repeat_tx = True
        rx_policy.window_size = 1024
        self._rx_srtp = Session(rx_policy)

        tx_policy = Policy(
            key=srtp_tx_key,
            ssrc_type=Policy.SSRC_ANY_OUTBOUND,
            srtp_profile=srtp_profile.libsrtp_profile,
        )
        tx_policy.allow_repeat_tx = True
        tx_policy.window_size = 1024
        self._tx_srtp = Session(tx_policy)

        # start data pump
        self.__log_debug("- DTLS handshake complete")
        self._set_state(State.CONNECTED)
        self._task = asyncio.ensure_future(self.__run())

    async def stop(self) -> None:
        """
        Stop and close the DTLS transport.
        """
        if self._task is not None:
            self._task.cancel()
            self._task = None

        if self._ssl and self._state in [State.CONNECTING, State.CONNECTED]:
            try:
                self._ssl.shutdown()
            except SSL.Error:
                pass
            try:
                await self._write_ssl()
            except ConnectionError:
                pass
            self.__log_debug("- DTLS shutdown complete")

    async def __run(self) -> None:
        try:
            while True:
                await self._recv_next()
        except ConnectionError:
            for receiver in self._rtp_router.receivers:
                receiver._handle_disconnect()
        except Exception as exc:
            if not isinstance(exc, asyncio.CancelledError):
                self.__log_warning(traceback.format_exc())
            raise exc
        finally:
            self._set_state(State.CLOSED)

    def _get_stats(self) -> RTCStatsReport:
        report = RTCStatsReport()
        report.add(
            RTCTransportStats(
                # RTCStats
                timestamp=clock.current_datetime(),
                type="transport",
                id=self._stats_id,
                # RTCTransportStats,
                packetsSent=self.__tx_packets,
                packetsReceived=self.__rx_packets,
                bytesSent=self.__tx_bytes,
                bytesReceived=self.__rx_bytes,
                iceRole=self.transport.role,
                dtlsState=self.state,
            )
        )
        return report

    async def _handle_rtcp_data(self, data: bytes) -> None:
        try:
            packets = RtcpPacket.parse(data)
        except ValueError as exc:
            self.__log_debug("x RTCP parsing failed: %s", exc)
            return

        for packet in packets:
            # route RTCP packet
            for recipient in self._rtp_router.route_rtcp(packet):
                await recipient._handle_rtcp_packet(packet)

    async def _handle_rtp_data(self, data: bytes, arrival_time_ms: int) -> None:
        try:
            packet = RtpPacket.parse(data, self._rtp_header_extensions_map)
        except ValueError as exc:
            self.__log_debug("x RTP parsing failed: %s", exc)
            return

        # route RTP packet
        receiver = self._rtp_router.route_rtp(packet)
        if receiver is not None:
            await receiver._handle_rtp_packet(packet, arrival_time_ms=arrival_time_ms)

    async def _recv_next(self) -> None:
        # get timeout
        timeout = None
        if not self.encrypted:
            timeout = self._ssl.DTLSv1_get_timeout()

        # receive next datagram
        if timeout is not None:
            try:
                data = await asyncio.wait_for(self.transport._recv(), timeout=timeout)
            except asyncio.TimeoutError:
                self.__log_debug("x DTLS handling timeout")
                self._ssl.DTLSv1_handle_timeout()
                await self._write_ssl()
                return
        else:
            data = await self.transport._recv()

        self.__rx_bytes += len(data)
        self.__rx_packets += 1

        first_byte = data[0]
        if first_byte > 19 and first_byte < 64:
            # DTLS
            self._ssl.bio_write(data)
            try:
                data = self._ssl.recv(1500)
            except SSL.ZeroReturnError:
                data = None
            except SSL.Error:
                data = b""
            await self._write_ssl()
            if data is None:
                self.__log_debug("- DTLS shutdown by remote party")
                raise ConnectionError
            elif data and self._data_receiver:
                await self._data_receiver._handle_data(data)
        elif first_byte > 127 and first_byte < 192 and self._rx_srtp:
            # SRTP / SRTCP
            arrival_time_ms = clock.current_ms()
            try:
                if is_rtcp(data):
                    data = self._rx_srtp.unprotect_rtcp(data)
                    await self._handle_rtcp_data(data)
                else:
                    data = self._rx_srtp.unprotect(data)
                    await self._handle_rtp_data(data, arrival_time_ms=arrival_time_ms)
            except pylibsrtp.Error as exc:
                self.__log_debug("x SRTP unprotect failed: %s", exc)

    def _register_data_receiver(self, receiver) -> None:
        assert self._data_receiver is None
        self._data_receiver = receiver

    def _register_rtp_receiver(
        self, receiver, parameters: RTCRtpReceiveParameters
    ) -> None:
        ssrcs = set()
        for encoding in parameters.encodings:
            ssrcs.add(encoding.ssrc)

        self._rtp_header_extensions_map.configure(parameters)
        self._rtp_router.register_receiver(
            receiver,
            ssrcs=list(ssrcs),
            payload_types=[codec.payloadType for codec in parameters.codecs],
            mid=parameters.muxId,
        )

    def _register_rtp_sender(self, sender, parameters: RTCRtpSendParameters) -> None:
        self._rtp_header_extensions_map.configure(parameters)
        self._rtp_router.register_sender(sender, ssrc=sender._ssrc)

    async def _send_data(self, data: bytes) -> None:
        if self._state != State.CONNECTED:
            raise ConnectionError("Cannot send encrypted data, not connected")

        self._ssl.send(data)
        await self._write_ssl()

    async def _send_rtp(self, data: bytes) -> None:
        if self._state != State.CONNECTED:
            raise ConnectionError("Cannot send encrypted RTP, not connected")

        if is_rtcp(data):
            data = self._tx_srtp.protect_rtcp(data)
        else:
            data = self._tx_srtp.protect(data)
        await self.transport._send(data)
        self.__tx_bytes += len(data)
        self.__tx_packets += 1

    def _set_role(self, role: str) -> None:
        self._role = role

    def _set_state(self, state: State) -> None:
        if state != self._state:
            self.__log_debug("- %s -> %s", self._state, state)
            self._state = state
            self.emit("statechange")

    def _unregister_data_receiver(self, receiver) -> None:
        if self._data_receiver == receiver:
            self._data_receiver = None

    def _unregister_rtp_receiver(self, receiver) -> None:
        self._rtp_router.unregister_receiver(receiver)

    def _unregister_rtp_sender(self, sender) -> None:
        self._rtp_router.unregister_sender(sender)

    async def _write_ssl(self) -> None:
        """
        Flush outgoing data which OpenSSL put in our BIO to the transport.
        """
        try:
            data = self._ssl.bio_read(1500)
        except SSL.Error:
            data = b""
        if data:
            await self.transport._send(data)
            self.__tx_bytes += len(data)
            self.__tx_packets += 1

    def __log_debug(self, msg: str, *args) -> None:
        logger.debug(f"RTCDtlsTransport(%s) {msg}", self._role, *args)

    def __log_warning(self, msg: str, *args) -> None:
        logger.warning(f"RTCDtlsTransport(%s) {msg}", self._role, *args)
