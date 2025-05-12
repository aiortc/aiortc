import asyncio
from unittest import TestCase

from aiortc import (
    RTCCertificate,
    RTCDtlsTransport,
    RTCIceGatherer,
    RTCIceTransport,
    RTCSctpTransport,
)

from .utils import asynctest


async def start_dtls_pair(
    ice_a: RTCIceTransport, ice_b: RTCIceTransport
) -> tuple[RTCDtlsTransport, RTCDtlsTransport]:
    dtls_a = RTCDtlsTransport(ice_a, [RTCCertificate.generateCertificate()])
    dtls_b = RTCDtlsTransport(ice_b, [RTCCertificate.generateCertificate()])

    await asyncio.gather(
        dtls_a.start(dtls_b.getLocalParameters()),
        dtls_b.start(dtls_a.getLocalParameters()),
    )

    return dtls_a, dtls_b


async def start_ice_pair() -> tuple[RTCIceTransport, RTCIceTransport]:
    ice_a = RTCIceTransport(gatherer=RTCIceGatherer())
    ice_b = RTCIceTransport(gatherer=RTCIceGatherer())

    await asyncio.gather(ice_a.iceGatherer.gather(), ice_b.iceGatherer.gather())

    for candidate in ice_b.iceGatherer.getLocalCandidates():
        await ice_a.addRemoteCandidate(candidate)
    for candidate in ice_a.iceGatherer.getLocalCandidates():
        await ice_b.addRemoteCandidate(candidate)
    await asyncio.gather(
        ice_a.start(ice_b.iceGatherer.getLocalParameters()),
        ice_b.start(ice_a.iceGatherer.getLocalParameters()),
    )

    return ice_a, ice_b


async def start_sctp_pair(
    dtls_a: RTCDtlsTransport, dtls_b: RTCDtlsTransport
) -> tuple[RTCSctpTransport, RTCSctpTransport]:
    sctp_a = RTCSctpTransport(dtls_a)
    sctp_b = RTCSctpTransport(dtls_b)

    await asyncio.gather(
        sctp_a.start(sctp_b.getCapabilities(), sctp_b.port),
        sctp_b.start(sctp_a.getCapabilities(), sctp_a.port),
    )

    return sctp_a, sctp_b


class OrtcTest(TestCase):
    @asynctest
    async def test_sctp(self) -> None:
        # start ICE transports
        ice_a, ice_b = await start_ice_pair()

        # start DTLS transports
        dtls_a, dtls_b = await start_dtls_pair(ice_a, ice_b)

        # start SCTP transports
        sctp_a, sctp_b = await start_sctp_pair(dtls_a, dtls_b)

        # stop SCTP transports
        await asyncio.gather(sctp_a.stop(), sctp_b.stop())

        # stop DTLS transports
        await asyncio.gather(dtls_a.stop(), dtls_b.stop())

        # stop ICE transports
        await asyncio.gather(ice_a.stop(), ice_b.stop())
