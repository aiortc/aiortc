import asyncio
import base64
import binascii
import datetime
import enum
import logging
import os
import struct

import attr
import pylibsrtp
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.bindings.openssl.binding import Binding
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (Encoding,
                                                          NoEncryption,
                                                          PrivateFormat)
from OpenSSL import crypto
from pyee import EventEmitter
from pylibsrtp import Policy, Session

from . import clock, rtp
from .rtp import RtcpPacket, RtpPacket, is_rtcp
from .stats import RTCStatsReport, RTCTransportStats

binding = Binding()
binding.init_static_locks()
ffi = binding.ffi
lib = binding.lib

SRTP_KEY_LEN = 16
SRTP_SALT_LEN = 14

logger = logging.getLogger('dtls')


class DtlsError(Exception):
    pass


def _openssl_assert(ok):
    if not ok:
        raise DtlsError('OpenSSL call failed')


def certificate_digest(x509):
    digest = lib.EVP_get_digestbyname(b'SHA256')
    _openssl_assert(digest != ffi.NULL)

    result_buffer = ffi.new('unsigned char[]', lib.EVP_MAX_MD_SIZE)
    result_length = ffi.new('unsigned int[]', 1)
    result_length[0] = len(result_buffer)

    digest_result = lib.X509_digest(x509, digest, result_buffer, result_length)
    assert digest_result == 1

    return b":".join([
        base64.b16encode(ch).upper() for ch
        in ffi.buffer(result_buffer, result_length[0])]).decode('ascii')


def generate_key():
    key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    key_pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=NoEncryption())
    return crypto.load_privatekey(crypto.FILETYPE_PEM, key_pem)


def generate_certificate(key):
    cert = crypto.X509()
    cert.get_subject().CN = binascii.hexlify(os.urandom(16)).decode('ascii')
    cert.gmtime_adj_notBefore(-86400)
    cert.gmtime_adj_notAfter(30 * 86400)
    cert.set_version(2)
    cert.set_serial_number(struct.unpack('!L', os.urandom(4))[0])
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(key)
    cert.sign(key, 'sha256')
    return cert


def get_srtp_key_salt(src, idx):
    key_start = idx * SRTP_KEY_LEN
    salt_start = 2 * SRTP_KEY_LEN + idx * SRTP_SALT_LEN
    return (
        src[key_start:key_start + SRTP_KEY_LEN] +
        src[salt_start:salt_start + SRTP_SALT_LEN]
    )


@ffi.callback('int(int, X509_STORE_CTX *)')
def verify_callback(x, y):
    return 1


def create_ssl_context(certificate):
    if hasattr(lib, 'DTLS_method'):
        # openssl >= 1.0.2
        method = lib.DTLS_method
    else:  # pragma: no cover
        # openssl < 1.0.2
        method = lib.DTLSv1_method
    ctx = lib.SSL_CTX_new(method())
    ctx = ffi.gc(ctx, lib.SSL_CTX_free)

    lib.SSL_CTX_set_verify(ctx, lib.SSL_VERIFY_PEER | lib.SSL_VERIFY_FAIL_IF_NO_PEER_CERT,
                           verify_callback)

    _openssl_assert(lib.SSL_CTX_use_certificate(ctx, certificate._cert._x509) == 1)
    _openssl_assert(lib.SSL_CTX_use_PrivateKey(ctx, certificate._key._pkey) == 1)
    _openssl_assert(lib.SSL_CTX_set_cipher_list(ctx, b'HIGH:!CAMELLIA:!aNULL') == 1)
    _openssl_assert(lib.SSL_CTX_set_tlsext_use_srtp(ctx, b'SRTP_AES128_CM_SHA1_80') == 0)
    _openssl_assert(lib.SSL_CTX_set_read_ahead(ctx, 1) == 0)
    return ctx


class State(enum.Enum):
    NEW = 0
    CONNECTING = 1
    CONNECTED = 2
    CLOSED = 3
    FAILED = 4


class RTCCertificate:
    """
    The :class:`RTCCertificate` interface enables the certificates used by an
    :class:`RTCDtlsTransport`.

    To generate a certificate and the corresponding private key use :func:`generateCertificate`.
    """
    def __init__(self, key, cert):
        self._key = key
        self._cert = cert

    @property
    def expires(self):
        """
        The date and time after which the certificate will be considered invalid.
        """
        not_after = self._cert.get_notAfter().decode('ascii')
        return datetime.datetime.strptime(not_after, '%Y%m%d%H%M%SZ').replace(
            tzinfo=datetime.timezone.utc)

    def getFingerprints(self):
        """
        Returns the list of certificate fingerprints, one of which is computed
        with the digest algorithm used in the certificate signature.
        """
        return [
            RTCDtlsFingerprint(algorithm='sha-256', value=certificate_digest(self._cert._x509))
        ]

    @classmethod
    def generateCertificate(cls):
        """
        Create and return an X.509 certificate and corresponding private key.

        :rtype: RTCCertificate
        """
        key = generate_key()
        cert = generate_certificate(key)
        return cls(key=key, cert=cert)


@attr.s
class RTCDtlsFingerprint:
    """
    The :class:`RTCDtlsFingerprint` dictionary includes the hash function
    algorithm and certificate fingerprint.
    """
    algorithm = attr.ib()
    "The hash function name, for instance `'sha-256'`."

    value = attr.ib()
    "The fingerprint value."


@attr.s
class RTCDtlsParameters:
    """
    The :class:`RTCDtlsParameters` dictionary includes information relating to
    DTLS configuration.
    """
    fingerprints = attr.ib(default=attr.Factory(list))
    "List of :class:`RTCDtlsFingerprint`, one fingerprint for each certificate."

    role = attr.ib(default='auto')
    "The DTLS role, with a default of auto."


class RtpRouter:
    def __init__(self):
        self.receivers = set()
        self.mid_table = {}
        self.ssrc_table = {}

    def register(self, receiver, parameters):
        self.receivers.add(receiver)
        if parameters.muxId:
            self.mid_table[parameters.muxId] = receiver
        if parameters.rtcp.ssrc:
            self.ssrc_table[parameters.rtcp.ssrc] = receiver

    def route(self, ssrc, mid=None):
        return self.ssrc_table.get(ssrc)

    def unregister(self, receiver):
        self.receivers.discard(receiver)
        self.__discard(self.mid_table, receiver)
        self.__discard(self.ssrc_table, receiver)

    def __discard(self, d, value):
        for k, v in list(d.items()):
            if v == value:
                d.pop(k)


class RTCDtlsTransport(EventEmitter):
    """
    The :class:`RTCDtlsTransport` object includes information relating to
    Datagram Transport Layer Security (DTLS) transport.

    :param: transport: An :class:`RTCIceTransport`.
    :param: certificates: A list of :class:`RTCCertificate` (only one is allowed currently).
    """
    def __init__(self, transport, certificates):
        assert len(certificates) == 1
        certificate = certificates[0]

        super().__init__()
        self.encrypted = False
        self._data_receiver = None
        self._role = 'auto'
        self._rtp_header_extensions_map = rtp.HeaderExtensionsMap()
        self._rtp_router = RtpRouter()
        self._rtp_senders = set()
        self._state = State.NEW
        self._stats_id = 'transport_' + str(id(self))
        self._task = None
        self._transport = transport

        # counters
        self.__rx_bytes = 0
        self.__rx_packets = 0
        self.__tx_bytes = 0
        self.__tx_packets = 0

        # SSL init
        self.__ctx = create_ssl_context(certificate)

        ssl = lib.SSL_new(self.__ctx)
        self.ssl = ffi.gc(ssl, lib.SSL_free)

        self.read_bio = lib.BIO_new(lib.BIO_s_mem())
        self.read_cdata = ffi.new('char[]', 1500)
        self.write_bio = lib.BIO_new(lib.BIO_s_mem())
        self.write_cdata = ffi.new('char[]', 1500)
        lib.SSL_set_bio(self.ssl, self.read_bio, self.write_bio)

        self.__local_certificate = certificate

    @property
    def state(self):
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

    def getLocalParameters(self):
        """
        Get the local parameters of the DTLS transport.

        :rtype: :class:`RTCDtlsParameters`
        """
        return RTCDtlsParameters(fingerprints=self.__local_certificate.getFingerprints())

    async def start(self, remoteParameters):
        """
        Start DTLS transport negotiation with the parameters of the remote
        DTLS transport.

        :param: remoteParameters: An :class:`RTCDtlsParameters`.
        """
        assert self._state == State.NEW
        assert len(remoteParameters.fingerprints)

        if self.transport.role == 'controlling':
            self._role = 'server'
            lib.SSL_set_accept_state(self.ssl)
        else:
            self._role = 'client'
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
                    self.__log_debug('x DTLS handshake failed (error %d)', error)
                    self._set_state(State.FAILED)
                    return
        except ConnectionError:
            self.__log_debug('x DTLS handshake failed (connection error)')
            self._set_state(State.FAILED)
            return

        # check remote fingerprint
        x509 = lib.SSL_get_peer_certificate(self.ssl)
        remote_fingerprint = certificate_digest(x509)
        fingerprint_is_valid = False
        for f in remoteParameters.fingerprints:
            if f.algorithm == 'sha-256' and f.value.lower() == remote_fingerprint.lower():
                fingerprint_is_valid = True
                break
        if not fingerprint_is_valid:
            self.__log_debug('x DTLS handshake failed (fingerprint mismatch)')
            self._set_state(State.FAILED)
            return

        # generate keying material
        buf = ffi.new('unsigned char[]', 2 * (SRTP_KEY_LEN + SRTP_SALT_LEN))
        extractor = b'EXTRACTOR-dtls_srtp'
        _openssl_assert(lib.SSL_export_keying_material(
            self.ssl, buf, len(buf), extractor, len(extractor), ffi.NULL, 0, 0) == 1)

        view = ffi.buffer(buf)
        if self._role == 'server':
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
        self.__log_debug('- DTLS handshake complete')
        self._set_state(State.CONNECTED)
        self._task = asyncio.ensure_future(self.__run())

    async def stop(self):
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
            self.__log_debug('- DTLS shutdown complete')

    async def __run(self):
        try:
            while True:
                await self._recv_next()
        except ConnectionError:
            for receiver in self._rtp_router.receivers:
                receiver._handle_disconnect()
        finally:
            self._set_state(State.CLOSED)

    def _get_stats(self):
        report = RTCStatsReport()
        report.add(RTCTransportStats(
            # RTCStats
            timestamp=clock.current_datetime(),
            type='transport',
            id=self._stats_id,
            # RTCTransportStats,
            packetsSent=self.__tx_packets,
            packetsReceived=self.__rx_packets,
            bytesSent=self.__tx_bytes,
            bytesReceived=self.__rx_bytes,
            iceRole=self.transport.role,
            dtlsState=self.state,
        ))
        return report

    async def _handle_data(self, data):
        if self._data_receiver:
            await self._data_receiver._handle_data(data)

    async def _handle_rtcp_data(self, data):
        packets = RtcpPacket.parse(data)
        for packet in packets:
            receiver = None
            if hasattr(packet, 'ssrc'):
                # SR and RR
                receiver = self._rtp_router.route(packet.ssrc)
            elif getattr(packet, 'chunks', None):
                # SDES
                receiver = self._rtp_router.route(packet.chunks[0].ssrc)
            elif getattr(packet, 'sources', None):
                # BYE
                receiver = self._rtp_router.route(packet.sources[0])
            if receiver is not None:
                await receiver._handle_rtcp_packet(packet)

    async def _handle_rtp_data(self, data, arrival_time_ms):
        packet = RtpPacket.parse(data, self._rtp_header_extensions_map)

        # route RTP packet
        receiver = self._rtp_router.route(packet.ssrc, mid=packet.extensions.mid)
        if receiver is not None:
            await receiver._handle_rtp_packet(packet, arrival_time_ms=arrival_time_ms)

    async def _recv_next(self):
        # get timeout
        ptv_sec = ffi.new('time_t *')
        ptv_usec = ffi.new('long *')
        if lib.Cryptography_DTLSv1_get_timeout(self.ssl, ptv_sec, ptv_usec):
            timeout = ptv_sec[0] + (ptv_usec[0] / 1000000)
        else:
            timeout = None

        try:
            data = await asyncio.wait_for(self.transport._recv(), timeout=timeout)
        except asyncio.TimeoutError:
            self.__log_debug('x DTLS handling timeout')
            lib.DTLSv1_handle_timeout(self.ssl)
            await self._write_ssl()
            return

        arrival_time_ms = clock.current_ms()
        self.__rx_bytes += len(data)
        self.__rx_packets += 1

        first_byte = data[0]
        if first_byte > 19 and first_byte < 64:
            # DTLS
            lib.BIO_write(self.read_bio, data, len(data))
            result = lib.SSL_read(self.ssl, self.read_cdata, len(self.read_cdata))
            await self._write_ssl()
            if result == 0:
                self.__log_debug('- DTLS shutdown by remote party')
                raise ConnectionError
            elif result > 0:
                data = ffi.buffer(self.read_cdata)[0:result]
                await self._handle_data(data)
        elif first_byte > 127 and first_byte < 192:
            # SRTP / SRTCP
            try:
                if is_rtcp(data):
                    data = self._rx_srtp.unprotect_rtcp(data)
                    await self._handle_rtcp_data(data)
                else:
                    data = self._rx_srtp.unprotect(data)
                    await self._handle_rtp_data(data, arrival_time_ms=arrival_time_ms)
            except pylibsrtp.Error as exc:
                self.__log_debug('x SRTP unprotect failed: %s', exc)

    def _register_data_receiver(self, receiver):
        assert self._data_receiver is None
        self._data_receiver = receiver

    def _register_rtp_receiver(self, receiver, parameters):
        self._rtp_header_extensions_map.configure(parameters)
        self._rtp_router.register(receiver, parameters)

    def _register_rtp_sender(self, sender, parameters):
        self._rtp_header_extensions_map.configure(parameters)
        self._rtp_senders.add(sender)

    async def _send_data(self, data):
        if self._state != State.CONNECTED:
            raise ConnectionError('Cannot send encrypted data, not connected')

        lib.SSL_write(self.ssl, data, len(data))
        await self._write_ssl()

    async def _send_rtp(self, data):
        if self._state != State.CONNECTED:
            raise ConnectionError('Cannot send encrypted RTP, not connected')

        if is_rtcp(data):
            data = self._tx_srtp.protect_rtcp(data)
        else:
            data = self._tx_srtp.protect(data)
        await self.transport._send(data)
        self.__tx_bytes += len(data)
        self.__tx_packets += 1

    def _set_state(self, state):
        if state != self._state:
            self.__log_debug('- %s -> %s', self._state, state)
            self._state = state
            self.emit('statechange')

    def _unregister_data_receiver(self, receiver):
        if self._data_receiver == receiver:
            self._data_receiver = None

    def _unregister_rtp_receiver(self, receiver):
        self._rtp_router.unregister(receiver)

    def _unregister_rtp_sender(self, sender):
        self._rtp_senders.discard(sender)

    async def _write_ssl(self):
        """
        Flush outgoing data which OpenSSL put in our BIO to the transport.
        """
        pending = lib.BIO_ctrl_pending(self.write_bio)
        if pending > 0:
            result = lib.BIO_read(self.write_bio, self.write_cdata, len(self.write_cdata))
            await self.transport._send(ffi.buffer(self.write_cdata)[0:result])
            self.__tx_bytes += result
            self.__tx_packets += 1

    def __log_debug(self, msg, *args):
        logger.debug(self._role + ' ' + msg, *args)
