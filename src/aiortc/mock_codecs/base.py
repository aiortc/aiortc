from abc import ABCMeta, abstractmethod

from ..jitterbuffer import JitterFrame


class Decoder(metaclass=ABCMeta):
    @abstractmethod
    def decode(self, encoded_frame: JitterFrame) -> list["Frame"]:
        pass  # pragma: no cover

class Encoder(metaclass=ABCMeta):
    @abstractmethod
    def encode(
        self, frame: "Frame", force_keyframe: bool = False
    ) -> tuple[list[bytes], int]:
        pass  # pragma: no cover

    @abstractmethod
    def pack(self, packet: "Packet") -> tuple[list[bytes], int]:
        pass  # pragma: no cover