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
)

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

    def log_event(self, *, category: str, event: str, data: Dict) -> None:
        self._events.append((time.time(), category, event, data))

    def packet_type(self, packet_type: int) -> str:
        return PACKET_TYPE_NAMES.get(packet_type & PACKET_TYPE_MASK, "1RTT")

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
                            event[1].upper(),  # draft-00
                            event[2].upper(),  # draft-00
                            event[3],
                        ),
                        self._events,
                    )
                ),
            }
            traces.append(trace)

        return {"qlog_version": "draft-00", "traces": traces}
