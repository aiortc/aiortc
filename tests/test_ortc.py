import asyncio
from unittest import TestCase

from aiortc import (RTCCertificate, RTCDtlsTransport, RTCIceGatherer,
                    RTCIceTransport, RTCSctpTransport)

from .utils import run


async def start_dtls_pair(ice_a, ice_b):
    dtls_a = RTCDtlsTransport(ice_a, [RTCCertificate.generateCertificate()])
    dtls_b = RTCDtlsTransport(ice_b, [RTCCertificate.generateCertificate()])

    await asyncio.gather(
        dtls_a.start(dtls_b.getLocalParameters()),
        dtls_b.start(dtls_a.getLocalParameters()))

    return dtls_a, dtls_b


async def start_ice_pair():
    ice_a = RTCIceTransport(gatherer=RTCIceGatherer())
    ice_b = RTCIceTransport(gatherer=RTCIceGatherer())

    await asyncio.gather(
        ice_a.iceGatherer.gather(),
        ice_b.iceGatherer.gather())

    for candidate in ice_b.iceGatherer.getLocalCandidates():
        ice_a.addRemoteCandidate(candidate)
    for candidate in ice_a.iceGatherer.getLocalCandidates():
        ice_b.addRemoteCandidate(candidate)
    await asyncio.gather(
        ice_a.start(ice_b.iceGatherer.getLocalParameters()),
        ice_b.start(ice_a.iceGatherer.getLocalParameters()))

    return ice_a, ice_b


async def start_sctp_pair(dtls_a, dtls_b):
    sctp_a = RTCSctpTransport(dtls_a)
    sctp_b = RTCSctpTransport(dtls_b)

    await asyncio.gather(
        sctp_a.start(sctp_b.getCapabilities(), sctp_b.port),
        sctp_b.start(sctp_a.getCapabilities(), sctp_a.port))

    return sctp_a, sctp_b


class OrtcTest(TestCase):
    def test_sctp(self):
        # start ICE transports
        ice_a, ice_b = run(start_ice_pair())

        # start DTLS transports
        dtls_a, dtls_b = run(start_dtls_pair(ice_a, ice_b))

        # start SCTP transports
        sctp_a, sctp_b = run(start_sctp_pair(dtls_a, dtls_b))

        # stop SCTP transports
        run(asyncio.gather(sctp_a.stop(), sctp_b.stop()))

        # stop DTLS transports
        run(asyncio.gather(dtls_a.stop(), dtls_b.stop()))

        # stop ICE transports
        run(asyncio.gather(ice_a.stop(), ice_b.stop()))
