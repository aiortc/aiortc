from dataclasses import dataclass


@dataclass
class RTCSessionDescription:
    """
    The :class:`RTCSessionDescription` dictionary describes one end of a
    connection and how it's configured.
    """

    sdp: str
    type: str

    def __post_init__(self) -> None:
        if self.type not in {"offer", "pranswer", "answer", "rollback"}:
            raise ValueError(
                "'type' must be in ['offer', 'pranswer', 'answer', 'rollback'] "
                f"(got '{self.type}')"
            )
