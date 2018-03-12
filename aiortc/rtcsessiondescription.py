import attr


@attr.s
class RTCSessionDescription:
    """
    The :class:`RTCSessionDescription` dictionary describes one end of a
    connection and how it's configured.
    """
    sdp = attr.ib()
    "A string containing the session description's SDP."

    type = attr.ib(validator=attr.validators.in_(['offer', 'pranswer', 'answer', 'rollback']))
    "A string describing the session description's type."
