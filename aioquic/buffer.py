from ._buffer import Buffer, BufferReadError, BufferWriteError  # noqa


def size_uint_var(value: int) -> int:
    """
    Returns the number of bytes required to encode the given value
    as a QUIC variable-length unsigned integer.
    """
    if value <= 0x3F:
        return 1
    elif value <= 0x3FFF:
        return 2
    elif value <= 0x3FFFFFFF:
        return 4
    elif value <= 0x3FFFFFFFFFFFFFFF:
        return 8
    else:
        raise ValueError("Integer is too big for a variable-length integer")
