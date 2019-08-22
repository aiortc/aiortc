import time
from collections import deque
from typing import Any, Deque, Dict, Tuple

from .packet import (
    PACKET_TYPE_HANDSHAKE,
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_MASK,
    PACKET_TYPE_ONE_RTT,
    PACKET_TYPE_RETRY,
    PACKET_TYPE_ZERO_RTT,
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


class QuicLogger:
    """
    A QUIC event logger.

    Events are logged in the format defined by qlog draft-00.

    See: https://tools.ietf.org/html/draft-marx-qlog-main-schema-00
    """

    def __init__(self) -> None:
        self._events: Deque[Tuple[float, str, str, Dict[str, Any]]] = deque()
        self._vantage_point = {"name": "aioquic", "type": "unknown"}

    def encode_ack_frame(self, rangeset: RangeSet, delay: float) -> Dict:
        return {
            "ack_delay": str(int(delay * 1000)),  # convert to ms
            "acked_ranges": [[str(x.start), str(x.stop - 1)] for x in rangeset],
            "frame_type": "ACK",
        }

    def encode_crypto_frame(self, frame: QuicStreamFrame) -> Dict:
        return {
            "fin": frame.fin,
            "frame_type": "CRYPTO",
            "length": str(len(frame.data)),
            "offset": str(frame.offset),
        }

    def encode_padding_frame(self) -> Dict:
        return {"frame_type": "PADDING"}

    def encode_ping_frame(self) -> Dict:
        return {"frame_type": "PING"}

    def encode_stream_frame(self, frame: QuicStreamFrame, stream_id: int) -> Dict:
        return {
            "fin": frame.fin,
            "frame_type": "STREAM",
            "id": str(stream_id),
            "length": str(len(frame.data)),
            "offset": str(frame.offset),
        }

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
