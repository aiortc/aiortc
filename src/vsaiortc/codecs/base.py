from abc import ABCMeta, abstractmethod
from typing import List, Tuple

from av.frame import Frame
from av.packet import Packet

from ..jitterbuffer import JitterFrame


class Decoder(metaclass=ABCMeta):
    @abstractmethod
    def decode(self, encoded_frame: JitterFrame) -> List[Frame]:
        pass  # pragma: no cover


class Encoder(metaclass=ABCMeta):
    @abstractmethod
    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> Tuple[List[bytes], int]:
        pass  # pragma: no cover

    @abstractmethod
    def pack(self, packet: Packet) -> Tuple[List[bytes], int]:
        pass  # pragma: no cover
