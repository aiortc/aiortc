from typing import List, Optional

from .buffer import Buffer, push_bytes, push_uint8, push_uint16
from .crypto import CryptoPair
from .packet import PACKET_NUMBER_MAX_SIZE, QuicHeader, is_long_header, push_quic_header

PACKET_MAX_SIZE = 1280
PACKET_NUMBER_SEND_SIZE = 2


def push_packet_number(buf: Buffer, packet_number: int) -> None:
    """
    Packet numbers are truncated and encoded using 1, 2 or 4 bytes.

    We choose to use 2 bytes which provides a good tradeoff between encoded
    size and the "window" of packets we can represent.
    """
    push_uint16(buf, packet_number & 0xFFFF)


class QuicPacketBuilder:
    """
    Helper for building QUIC packets.
    """

    def __init__(
        self,
        host_cid: bytes,
        packet_number: int,
        peer_cid: bytes,
        peer_token: bytes,
        spin_bit: bool,
        version: int,
    ):
        self._host_cid = host_cid
        self._peer_cid = peer_cid
        self._peer_token = peer_token
        self._spin_bit = spin_bit
        self._version = version

        # assembled datagrams
        self._datagrams: List[bytes] = []

        # current packet
        self._crypto: Optional[CryptoPair] = None
        self._header_size = 0
        self._packet_number = packet_number
        self._packet_start = 0
        self._packet_type = 0

        self.buffer = Buffer(PACKET_MAX_SIZE)

    @property
    def packet_number(self) -> int:
        """
        Returns the packet number for the next packet.
        """
        return self._packet_number

    @property
    def remaining_space(self) -> int:
        """
        Returns the remaining number of bytes which can be used in
        the current packet.
        """
        return self.buffer.capacity - self.buffer.tell() - self._crypto.aead_tag_size

    def flush(self) -> List[bytes]:
        """
        Returns the assembled datagrams.
        """
        self._flush_current_datagram()
        datagrams = self._datagrams
        self._datagrams = []
        return datagrams

    def start_packet(self, packet_type: int, crypto: CryptoPair) -> None:
        """
        Starts a new packet.
        """
        # if there is too little space remaining, start a new datagram
        # FIXME: the limit is arbitrary!
        if self.buffer.capacity - self.buffer.tell() < 128:
            self._flush_current_datagram()

        self._packet_start = self.buffer.tell()

        # write header
        if is_long_header(packet_type):
            push_quic_header(
                self.buffer,
                QuicHeader(
                    version=self._version,
                    packet_type=packet_type | (PACKET_NUMBER_SEND_SIZE - 1),
                    destination_cid=self._peer_cid,
                    source_cid=self._host_cid,
                    token=self._peer_token,
                ),
            )
        else:
            push_uint8(
                self.buffer,
                packet_type
                | (self._spin_bit << 5)
                | (crypto.key_phase << 2)
                | (PACKET_NUMBER_SEND_SIZE - 1),
            )
            push_bytes(self.buffer, self._peer_cid)
            push_packet_number(self.buffer, self._packet_number)

        self._crypto = crypto
        self._header_size = self.buffer.tell() - self._packet_start
        self._packet_type = packet_type

    def end_packet(self) -> bool:
        """
        Ends the current packet.

        Returns `True` if the packet contains data, `False` otherwise.
        """
        buf = self.buffer
        empty = True
        packet_size = buf.tell() - self._packet_start
        if packet_size > self._header_size:
            empty = False

            if is_long_header(self._packet_type):
                # finalize length
                buf.seek(
                    self._packet_start + self._header_size - PACKET_NUMBER_SEND_SIZE - 2
                )
                length = (
                    packet_size - self._header_size + 2 + self._crypto.aead_tag_size
                )
                push_uint16(buf, length | 0x4000)
                push_packet_number(buf, self._packet_number)
                buf.seek(packet_size)
            else:
                # check whether we need padding
                padding_size = (
                    PACKET_NUMBER_MAX_SIZE
                    - PACKET_NUMBER_SEND_SIZE
                    + self._header_size
                    - packet_size
                )
                if padding_size > 0:
                    push_bytes(buf, bytes(padding_size))
                    packet_size += padding_size

            # encrypt in place
            plain = buf.data_slice(self._packet_start, self._packet_start + packet_size)
            self.buffer.seek(self._packet_start)
            push_bytes(
                self.buffer,
                self._crypto.encrypt_packet(
                    plain[0 : self._header_size], plain[self._header_size : packet_size]
                ),
            )

            # short header packets cannot be coallesced, we need a new datagram
            if not is_long_header(self._packet_type):
                self._flush_current_datagram()

            self._packet_number += 1
        else:
            # "cancel" the packet
            self.buffer.seek(self._packet_start)

        self._crypto = None

        return not empty

    def _flush_current_datagram(self) -> None:
        if self.buffer.tell():
            self._datagrams.append(self.buffer.data)
            self.buffer.seek(0)
