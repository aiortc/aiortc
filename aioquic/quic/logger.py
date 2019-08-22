import binascii
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

from .packet import (
    PACKET_TYPE_HANDSHAKE,
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_MASK,
    PACKET_TYPE_ONE_RTT,
    PACKET_TYPE_RETRY,
    PACKET_TYPE_ZERO_RTT,
    QuicNewConnectionIdFrame,
    QuicStreamFrame,
)
from .rangeset import RangeSet

PACKET_TYPE_NAMES = {
    PACKET_TYPE_INITIAL: "INITIAL",
    PACKET_TYPE_HANDSHAKE: "HANDSHAKE",
    PACKET_TYPE_ZERO_RTT: "0RTT",
    PACKET_TYPE_ONE_RTT: "1RTT",
    PACKET_TYPE_RETRY: "RETRY",
}


def hexdump(data: bytes) -> str:
    return binascii.hexlify(data).decode("ascii")


class QuicLogger:
    """
    A QUIC event logger.

    Events are logged in the format defined by qlog draft-00.

    See: https://tools.ietf.org/html/draft-marx-qlog-main-schema-00
    """

    def __init__(self) -> None:
        self._events: Deque[Tuple[float, str, str, Dict[str, Any]]] = deque()
        self._vantage_point = {"name": "aioquic", "type": "unknown"}

    def encode_ack_frame(self, ranges: RangeSet, delay: float) -> Dict:
        return {
            "ack_delay": str(int(delay * 1000)),  # convert to ms
            "acked_ranges": [[str(x.start), str(x.stop - 1)] for x in ranges],
            "frame_type": "ACK",
        }

    def encode_connection_close_frame(
        self, error_code: int, frame_type: Optional[int], reason_phrase: str
    ) -> Dict:
        attrs = {
            "error_code": error_code,
            "error_space": "application" if frame_type is None else "transport",
            "frame_type": "CONNECTION_CLOSE",
            "raw_error_code": error_code,
            "reason": reason_phrase,
        }
        if frame_type is not None:
            attrs["trigger_frame_type"] = frame_type

        return attrs

    def encode_crypto_frame(self, frame: QuicStreamFrame) -> Dict:
        return {
            "fin": frame.fin,
            "frame_type": "CRYPTO",
            "length": str(len(frame.data)),
            "offset": str(frame.offset),
        }

    def encode_data_blocked_frame(self, limit: int) -> Dict:
        return {"frame_type": "DATA_BLOCKED", "limit": str(limit)}

    def encode_max_data_frame(self, maximum: int) -> Dict:
        return {"frame_type": "MAX_DATA", "maximum": str(maximum)}

    def encode_max_stream_data_frame(self, maximum: int, stream_id: int) -> Dict:
        return {
            "frame_type": "MAX_STREAM_DATA",
            "id": str(stream_id),
            "maximum": str(maximum),
        }

    def encode_max_streams_frame(self, maximum: int) -> Dict:
        return {"frame_type": "MAX_STREAMS", "maximum": str(maximum)}

    def encode_new_connection_id_frame(self, frame: QuicNewConnectionIdFrame) -> Dict:
        return {
            "connection_id": hexdump(frame.connection_id),
            "frame_type": "NEW_CONNECTION_ID",
            "length": len(str(frame.connection_id)),
            "reset_token": hexdump(frame.stateless_reset_token),
            "retire_prior_to": str(frame.retire_prior_to),
            "sequence_number": str(frame.sequence_number),
        }

    def encode_new_token_frame(self, token: bytes) -> Dict:
        return {
            "frame_type": "NEW_TOKEN",
            "length": str(len(token)),
            "token": hexdump(token),
        }

    def encode_padding_frame(self) -> Dict:
        return {"frame_type": "PADDING"}

    def encode_path_challenge_frame(self, data: bytes) -> Dict:
        return {"data": hexdump(data), "frame_type": "PATH_CHALLENGE"}

    def encode_path_response_frame(self, data: bytes) -> Dict:
        return {"data": hexdump(data), "frame_type": "PATH_RESPONSE"}

    def encode_ping_frame(self) -> Dict:
        return {"frame_type": "PING"}

    def encode_reset_stream_frame(
        self, error_code: int, final_size: int, stream_id: int
    ) -> Dict:
        return {
            "error_code": error_code,
            "final_size": str(final_size),
            "frame_type": "RESET_STREAM",
        }

    def encode_retire_connection_id_frame(self, sequence_number: int) -> Dict:
        return {
            "frame_type": "RETIRE_CONNECTION_ID",
            "sequence_number": str(sequence_number),
        }

    def encode_stream_data_blocked_frame(self, limit: int, stream_id: int) -> Dict:
        return {
            "frame_type": "STREAM_DATA_BLOCKED",
            "limit": str(limit),
            "stream_id": str(stream_id),
        }

    def encode_stop_sending_frame(self, error_code: int, stream_id: int) -> Dict:
        return {
            "frame_type": "STOP_SENDING",
            "id": str(stream_id),
            "error_code": error_code,
        }

    def encode_stream_frame(self, frame: QuicStreamFrame, stream_id: int) -> Dict:
        return {
            "fin": frame.fin,
            "frame_type": "STREAM",
            "id": str(stream_id),
            "length": str(len(frame.data)),
            "offset": str(frame.offset),
        }

    def encode_streams_blocked_frame(self, limit: int) -> Dict:
        return {"frame_type": "STREAMS_BLOCKED", "limit": str(limit)}

    def log_event(self, *, category: str, event: str, data: Dict) -> None:
        self._events.append((time.time(), category, event, data))

    def packet_type(self, packet_type: int) -> str:
        return PACKET_TYPE_NAMES.get(packet_type & PACKET_TYPE_MASK, "1RTT")

    def start_trace(self, is_client: bool) -> None:
        self._vantage_point["type"] = "CLIENT" if is_client else "SERVER"

    def to_dict(self) -> Dict[str, Any]:
        """
        Return the trace as a dictionary which can be written as JSON.
        """
        traces = []
        if self._events:
            reference_time = self._events[0][0]
            trace = {
                "common_fields": {"reference_time": "%d" % (reference_time * 1000)},
                "event_fields": ["relative_time", "CATEGORY", "EVENT_TYPE", "DATA"],
                "events": list(
                    map(
                        lambda event: (
                            "%d" % ((event[0] - reference_time) * 1000),
                            event[1],
                            event[2],
                            event[3],
                        ),
                        self._events,
                    )
                ),
                "vantage_point": self._vantage_point,
            }
            traces.append(trace)

        return {"qlog_version": "draft-00", "traces": traces}
