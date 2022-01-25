import asyncio
import base64
import binascii
import datetime
import enum
import logging
import os
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Type, TypeVar

import pylibsrtp
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.bindings.openssl.binding import Binding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
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

binding = Binding()
binding.init_static_locks()
ffi = binding.ffi
lib = binding.lib

SRTP_KEY_LEN = 16
SRTP_SALT_LEN = 14

CERTIFICATE_T = TypeVar("CERTIFICATE_T", bound="RTCCertificate")

logger = logging.getLogger(__name__)

assert lib.OpenSSL_version_num() >= 0x10002000, "OpenSSL 1.0.2 or better is required"


class DtlsError(Exception):
    pass


def _openssl_assert(ok: bool) -> None:
    if not ok:
        raise DtlsError("OpenSSL call failed")


def certificate_digest(x509) -> str:
    digest = lib.EVP_get_digestbyname(b"SHA256")
    _openssl_assert(digest != ffi.NULL)

    result_buffer = ffi.new("unsigned char[]", lib.EVP_MAX_MD_SIZE)
    result_length = ffi.new("unsigned int[]", 1)
    result_length[0] = len(result_buffer)

    digest_result = lib.X509_digest(x509, digest, result_buffer, result_length)
    assert digest_result == 1

    return b":".join(
        [
            base64.b16encode(ch).upper()
            for ch in ffi.buffer(result_buffer, result_length[0])
        ]
    ).decode("ascii")


def generate_certificate(key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    name = x509.Name(
        [
            x509.NameAttribute(
                x509.NameOID.COMMON_NAME,
                binascii.hexlify(os.urandom(16)).decode("ascii"),
            )
        ]
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=30))
    )
    return builder.sign(key, hashes.SHA256(), default_backend())


def get_error_queue() -> List[Tuple[str, str, str]]:
    errors = []

    def text(charp) -> str:
        return ffi.string(charp).decode("utf-8") if charp else ""

    while True:
        error = lib.ERR_get_error()
        if error == 0:
            break

        errors.append(
            (
                text(lib.ERR_lib_error_string(error)),
                text(lib.ERR_func_error_string(error)),
                text(lib.ERR_reason_error_string(error)),
            )
        )

    return errors


def get_srtp_key_salt(src, idx: int) -> bytes:
    key_start = idx * SRTP_KEY_LEN
    salt_start = 2 * SRTP_KEY_LEN + idx * SRTP_SALT_LEN
    return (
        src[key_start : key_start + SRTP_KEY_LEN]
        + src[salt_start : salt_start + SRTP_SALT_LEN]
    )


@ffi.callback("int(int, X509_STORE_CTX *)")
def verify_callback(x, y):
    return 1


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

    To generate a certificate and the corresponding private key use :func:`generateCertificate`.
    """

    def __init__(self, key: ec.EllipticCurvePrivateKey, cert: x509.Certificate) -> None:
        self._key = key
        self._cert = cert

    @property
    def expires(self) -> datetime.datetime:
        """
        The date and time after which the certificate will be considered invalid.
        """
        return self._cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)

    def getFingerprints(self) -> List[RTCDtlsFingerprint]:
        """
        Returns the list of certificate fingerprints, one of which is computed
        with the digest algorithm used in the certificate signature.
        """
        return [
            RTCDtlsFingerprint(
                algorithm="sha-256",
                value=certificate_digest(self._cert._x509),  # type: ignore
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
        return cls(key=key, cert=cert)

    def _create_ssl_context(self) -> Any:
        ctx = lib.SSL_CTX_new(lib.DTLS_method())
        ctx = ffi.gc(ctx, lib.SSL_CTX_free)

        lib.SSL_CTX_set_verify(
            ctx,
            lib.SSL_VERIFY_PEER | lib.SSL_VERIFY_FAIL_IF_NO_PEER_CERT,
            verify_callback,
        )

        _openssl_assert(lib.SSL_CTX_use_certificate(ctx, self._cert._x509) == 1)  # type: ignore
        _openssl_assert(lib.SSL_CTX_use_PrivateKey(ctx, self._key._evp_pkey) == 1)  # type: ignore
        _openssl_assert(lib.SSL_CTX_set_cipher_list(ctx, b"HIGH:!CAMELLIA:!aNULL") == 1)
        _openssl_assert(
            lib.SSL_CTX_set_tlsext_use_srtp(ctx, b"SRTP_AES128_CM_SHA1_80") == 0
        )
        _openssl_assert(lib.SSL_CTX_set_read_ahead(ctx, 1) == 0)

        # Configure elliptic curve for ECDHE in server mode for OpenSSL < 1.1.0
        if lib.OpenSSL_version_num() < 0x10100000:  # pragma: no cover
            lib.SSL_CTX_set_ecdh_auto(ctx, 1)

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
    :param certificates: A list of :class:`RTCCertificate` (only one is allowed currently).
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

        # SSL init
        self.__ctx = certificate._create_ssl_context()

        ssl = lib.SSL_new(self.__ctx)
        self.ssl = ffi.gc(ssl, lib.SSL_free)

        self.read_bio = lib.BIO_new(lib.BIO_s_mem())
        self.read_cdata = ffi.new("char[]", 1500)
        self.write_bio = lib.BIO_new(lib.BIO_s_mem())
        self.write_cdata = ffi.new("char[]", 1500)
        lib.SSL_set_bio(self.ssl, self.read_bio, self.write_bio)

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

        if self._role == "server":
            lib.SSL_set_accept_state(self.ssl)
        else:
            lib.SSL_set_connect_state(self.ssl)

        self._set_state(State.CONNECTING)
        try:
            while not self.encrypted:
                result = lib.SSL_do_handshake(self.ssl)
                await self._write_ssl()

                if result > 0:
                    self.encrypted = True
                    break

                error = lib.SSL_get_error(self.ssl, result)
                if error == lib.SSL_ERROR_WANT_READ:
                    await self._recv_next()
                else:
                    self.__log_debug("x DTLS handshake failed (error %d)", error)
                    for info in get_error_queue():
                        self.__log_debug("x %s", ":".join(info))
                    self._set_state(State.FAILED)
                    return
        except ConnectionError:
            self.__log_debug("x DTLS handshake failed (connection error)")
            self._set_state(State.FAILED)
            return

        # check remote fingerprint
        x509 = lib.SSL_get_peer_certificate(self.ssl)
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
        buf = ffi.new("unsigned char[]", 2 * (SRTP_KEY_LEN + SRTP_SALT_LEN))
        extractor = b"EXTRACTOR-dtls_srtp"
        _openssl_assert(
            lib.SSL_export_keying_material(
                self.ssl, buf, len(buf), extractor, len(extractor), ffi.NULL, 0, 0
            )
            == 1
        )

        view = ffi.buffer(buf)
        if self._role == "server":
            srtp_tx_key = get_srtp_key_salt(view, 1)
            srtp_rx_key = get_srtp_key_salt(view, 0)
        else:
            srtp_tx_key = get_srtp_key_salt(view, 0)
            srtp_rx_key = get_srtp_key_salt(view, 1)

        rx_policy = Policy(key=srtp_rx_key, ssrc_type=Policy.SSRC_ANY_INBOUND)
        rx_policy.allow_repeat_tx = True
        rx_policy.window_size = 1024
        self._rx_srtp = Session(rx_policy)

        tx_policy = Policy(key=srtp_tx_key, ssrc_type=Policy.SSRC_ANY_OUTBOUND)
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

        if self._state in [State.CONNECTING, State.CONNECTED]:
            lib.SSL_shutdown(self.ssl)
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
            ptv_sec = ffi.new("time_t *")
            ptv_usec = ffi.new("long *")
            if lib.Cryptography_DTLSv1_get_timeout(self.ssl, ptv_sec, ptv_usec):
                timeout = ptv_sec[0] + (ptv_usec[0] / 1000000)

        # receive next datagram
        if timeout is not None:
            try:
                data = await asyncio.wait_for(self.transport._recv(), timeout=timeout)
            except asyncio.TimeoutError:
                self.__log_debug("x DTLS handling timeout")
                lib.DTLSv1_handle_timeout(self.ssl)
                await self._write_ssl()
                return
        else:
            data = await self.transport._recv()

        self.__rx_bytes += len(data)
        self.__rx_packets += 1

        first_byte = data[0]
        if first_byte > 19 and first_byte < 64:
            # DTLS
            lib.BIO_write(self.read_bio, data, len(data))
            result = lib.SSL_read(self.ssl, self.read_cdata, len(self.read_cdata))
            await self._write_ssl()
            if result == 0:
                self.__log_debug("- DTLS shutdown by remote party")
                raise ConnectionError
            elif result > 0 and self._data_receiver:
                data = ffi.buffer(self.read_cdata)[0:result]
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

        lib.SSL_write(self.ssl, data, len(data))
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
        pending = lib.BIO_ctrl_pending(self.write_bio)
        if pending > 0:
            result = lib.BIO_read(
                self.write_bio, self.write_cdata, len(self.write_cdata)
            )
            await self.transport._send(ffi.buffer(self.write_cdata)[0:result])
            self.__tx_bytes += result
            self.__tx_packets += 1

    def __log_debug(self, msg: str, *args) -> None:
        logger.debug(f"RTCDtlsTransport(%s) {msg}", self._role, *args)

    def __log_warning(self, msg: str, *args) -> None:
        logger.warning(f"RTCDtlsTransport(%s) {msg}", self._role, *args)
