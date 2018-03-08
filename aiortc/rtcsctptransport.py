class RTCSctpTransport:
    def __init__(self, transport):
        self.__transport = transport

    @property
    def transport(self):
        return self.__transport
