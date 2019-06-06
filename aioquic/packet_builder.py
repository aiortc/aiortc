from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, List, Optional, Sequence, Tuple

from .buffer import (
    Buffer,
    push_bytes,
    push_uint8,
    push_uint16,
    push_uint32,
    push_uint_var,
    size_uint_var,
)
from .crypto import CryptoPair
from .packet import (
    NON_ACK_ELICITING_FRAME_TYPES,
    PACKET_NUMBER_MAX_SIZE,
    PACKET_TYPE_HANDSHAKE,
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_MASK,
    QuicFrameType,
    encode_cid_length,
    is_long_header,
)
from .tls import Epoch

PACKET_MAX_SIZE = 1280
PACKET_LENGTH_SEND_SIZE = 2
PACKET_NUMBER_SEND_SIZE = 2


def push_packet_number(buf: Buffer, packet_number: int) -> None:
    """
    Packet numbers are truncated and encoded using 1, 2 or 4 bytes.

    We choose to use 2 bytes which provides a good tradeoff between encoded
    size and the "window" of packets we can represent.
    """
    push_uint16(buf, packet_number & 0xFFFF)


QuicDeliveryHandler = Callable[..., None]


class QuicDeliveryState(Enum):
    ACKED = 0
    LOST = 1
    EXPIRED = 2


@dataclass
class QuicSentPacket:
    epoch: Epoch
    in_flight: bool
    is_ack_eliciting: bool
    is_crypto_packet: bool
    packet_number: int
    sent_time: Optional[float] = None
    sent_bytes: int = 0

    delivery_handlers: List[Tuple[QuicDeliveryHandler, Any]] = field(
        default_factory=list
    )


class QuicPacketBuilder:
    """
    Helper for building QUIC packets.
    """

    def __init__(
        self,
        *,
        host_cid: bytes,
        peer_cid: bytes,
        version: int,
        pad_first_datagram: bool = False,
        packet_number: int = 0,
        peer_token: bytes = b"",
        spin_bit: bool = False,
    ):
        self._host_cid = host_cid
        self._pad_first_datagram = pad_first_datagram
        self._peer_cid = peer_cid
        self._peer_token = peer_token
        self._spin_bit = spin_bit
        self._version = version

        # assembled datagrams and packets
        self._ack_eliciting = False
        self._datagrams: List[bytes] = []
        self._packets: List[QuicSentPacket] = []
        self._total_bytes = 0

        # current packet
        self._header_size = 0
        self._packet: Optional[QuicSentPacket] = None
        self._packet_crypto: Optional[CryptoPair] = None
        self._packet_epoch: Optional[Epoch] = None
        self._packet_number = packet_number
        self._packet_start = 0
        self._packet_type = 0

        self.buffer = Buffer(PACKET_MAX_SIZE)

    @property
    def flight_bytes(self) -> int:
        """
        Returns the total number of bytes which will count towards
        congestion control.
        """
        total = self._total_bytes
        if self._ack_eliciting:
            total += self.buffer.tell()
        return total

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
        return (
            self.buffer.capacity
            - self.buffer.tell()
            - self._packet_crypto.aead_tag_size
        )

    def flush(self) -> Tuple[List[bytes], List[QuicSentPacket]]:
        """
        Returns the assembled datagrams.
        """
        self._flush_current_datagram()
        datagrams = self._datagrams
        packets = self._packets
        self._datagrams = []
        self._packets = []
        return datagrams, packets

    def start_frame(
        self,
        frame_type: int,
        handler: Optional[QuicDeliveryHandler] = None,
        args: Sequence[Any] = [],
    ) -> None:
        """
        Starts a new frame.
        """
        push_uint_var(self.buffer, frame_type)
        if frame_type not in NON_ACK_ELICITING_FRAME_TYPES:
            # FIXME: in_flight != is_ack_eliciting
            self._packet.in_flight = True
            self._packet.is_ack_eliciting = True
            self._ack_eliciting = True
        if frame_type == QuicFrameType.CRYPTO:
            self._packet.is_crypto_packet = True
        if handler is not None:
            self._packet.delivery_handlers.append((handler, args))

    def start_packet(self, packet_type: int, crypto: CryptoPair) -> None:
        """
        Starts a new packet.
        """
        buf = self.buffer
        self._ack_eliciting = False

        # if there is too little space remaining, start a new datagram
        # FIXME: the limit is arbitrary!
        if buf.capacity - buf.tell() < 128:
            self._flush_current_datagram()

        # determine ack epoch
        if packet_type == PACKET_TYPE_INITIAL:
            epoch = Epoch.INITIAL
        elif packet_type == PACKET_TYPE_HANDSHAKE:
            epoch = Epoch.HANDSHAKE
        else:
            epoch = Epoch.ONE_RTT

        self._packet = QuicSentPacket(
            epoch=epoch,
            in_flight=False,
            is_ack_eliciting=False,
            is_crypto_packet=False,
            packet_number=self._packet_number,
        )
        self._packet_crypto = crypto
        self._packet_epoch = epoch
        self._packet_start = buf.tell()
        self._packet_type = packet_type

        # calculate header size
        if is_long_header(packet_type):
            self._header_size = 10 + len(self._peer_cid) + len(self._host_cid)
            if (packet_type & PACKET_TYPE_MASK) == PACKET_TYPE_INITIAL:
                token_length = len(self._peer_token)
                self._header_size += size_uint_var(token_length) + token_length
        else:
            self._header_size = 3 + len(self._peer_cid)

        buf.seek(self._packet_start + self._header_size)

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

            # pad initial datagram
            if self._pad_first_datagram:
                push_bytes(buf, bytes(self.remaining_space))
                packet_size = buf.tell() - self._packet_start
                self._pad_first_datagram = False

            # write header
            if is_long_header(self._packet_type):
                length = (
                    packet_size
                    - self._header_size
                    + PACKET_NUMBER_SEND_SIZE
                    + self._packet_crypto.aead_tag_size
                )

                buf.seek(self._packet_start)
                push_uint8(buf, self._packet_type | (PACKET_NUMBER_SEND_SIZE - 1))
                push_uint32(buf, self._version)
                push_uint8(
                    buf,
                    (encode_cid_length(len(self._peer_cid)) << 4)
                    | encode_cid_length(len(self._host_cid)),
                )
                push_bytes(buf, self._peer_cid)
                push_bytes(buf, self._host_cid)
                if (self._packet_type & PACKET_TYPE_MASK) == PACKET_TYPE_INITIAL:
                    push_uint_var(buf, len(self._peer_token))
                    push_bytes(buf, self._peer_token)
                push_uint16(buf, length | 0x4000)
                push_packet_number(buf, self._packet_number)
            else:
                buf.seek(self._packet_start)
                push_uint8(
                    buf,
                    self._packet_type
                    | (self._spin_bit << 5)
                    | (self._packet_crypto.key_phase << 2)
                    | (PACKET_NUMBER_SEND_SIZE - 1),
                )
                push_bytes(buf, self._peer_cid)
                push_packet_number(buf, self._packet_number)

                # check whether we need padding
                padding_size = (
                    PACKET_NUMBER_MAX_SIZE
                    - PACKET_NUMBER_SEND_SIZE
                    + self._header_size
                    - packet_size
                )
                if padding_size > 0:
                    buf.seek(self._packet_start + packet_size)
                    push_bytes(buf, bytes(padding_size))
                    packet_size += padding_size

            # encrypt in place
            plain = buf.data_slice(self._packet_start, self._packet_start + packet_size)
            buf.seek(self._packet_start)
            push_bytes(
                buf,
                self._packet_crypto.encrypt_packet(
                    plain[0 : self._header_size], plain[self._header_size : packet_size]
                ),
            )
            self._packet.sent_bytes = buf.tell() - self._packet_start
            self._packets.append(self._packet)

            # short header packets cannot be coallesced, we need a new datagram
            if not is_long_header(self._packet_type):
                self._flush_current_datagram()

            self._packet_number += 1
        else:
            # "cancel" the packet
            buf.seek(self._packet_start)

        self._packet = None

        return not empty

    def _flush_current_datagram(self) -> None:
        if self.buffer.tell():
            self._datagrams.append(self.buffer.data)
            if self._ack_eliciting:
                self._total_bytes += self.buffer.tell()
            self.buffer.seek(0)
