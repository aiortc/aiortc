from .exceptions import InvalidStateError


class RTCSctpTransport:
    def __init__(self, transport, port=5000):
        if transport.state == 'closed':
            raise InvalidStateError

        self.__transport = transport
        self.__port = port

    @property
    def port(self):
        return self.__port

    @property
    def transport(self):
        return self.__transport
