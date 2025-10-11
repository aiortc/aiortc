from abc import ABCMeta, abstractmethod

class Encoder(metaclass=ABCMeta):
    @abstractmethod
    def encode(
        self, frame: "Frame", force_keyframe: bool = False
    ) -> tuple[list[bytes], int]:
        pass  # pragma: no cover

    @abstractmethod
    def pack(self, packet: "Packet") -> tuple[list[bytes], int]:
        pass  # pragma: no cover