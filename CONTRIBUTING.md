# Contributing to aiortc

Thank you for your interest in improving aiortc! This document describes
how changes are expected to be made, whether they are written by hand or
with the help of a tool.

Please report security vulnerabilities privately, as described in
[SECURITY.md](SECURITY.md), not through issues or pull requests. Everyone
taking part in the project is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Getting started

aiortc is written in pure Python. Install it in editable mode, together
with the tools used by continuous integration:

```
pip install -e . -r requirements/lint.txt -r requirements/test.txt
```

Setting the `AIORTC_DEBUG` environment variable enables debug logging
while running the tests.

Work on a branch and open a pull request against `main`.

## Correctness

**Specifications are the authority.** When fixing a bug or implementing
new code, ground the implementation in the relevant specification and read
the actual text rather than relying on memory. The API follows the W3C
WebRTC specification, and the protocol stack follows the IETF RFCs, for
instance:

- JSEP and SDP: RFC 9429, 8866, 8843 (BUNDLE)
- DTLS and SRTP: RFC 6347, 5764, 3711
- RTP and RTCP: RFC 3550, 4585, and the payload formats such as RFC 7741
  (VP8), 6184 (H.264) and 7587 (Opus)
- SCTP and data channels: RFC 9260, 8261, 8831, 8832, 8841

Note where a MUST, SHOULD or MAY applies, and justify behaviour changes
in comments and commit messages.

**Make the smallest change that closes the gap.** Keep a fix to its stated
aim and reuse existing helpers rather than porting a specification's
pseudocode wholesale. List any further alignment with the specification as
separate follow-ups.

**Browsers are the interoperability reference.** aiortc is tested against
Chrome and Firefox. libwebrtc and Firefox are useful to understand what
peers actually do, and a link to the relevant change or bug is welcome
when a behaviour exists for interoperability. They are not a source of
truth where they deviate from the specifications.

**Review your changes critically.** Look for side effects on other code
paths, and treat the remote peer as untrusted: consider how a malicious
peer could abuse the new behaviour, in particular when parsing packets or
SDP.

## Scope

aiortc builds on separate projects for parts of the stack. Please report
issues in the project they belong to:

- ICE (candidate gathering, connectivity checks, mDNS):
  [aioice](https://github.com/aiortc/aioice)
- SRTP encryption: [pylibsrtp](https://github.com/aiortc/pylibsrtp)
- Media decoding, encoding and file I/O: [PyAV](https://github.com/PyAV-Org/PyAV)

GitHub issues are not the place for usage questions, especially questions
about `asyncio` rather than aiortc. A good question can often be written as
a suggestion to improve the documentation.

## Code style

Readability is one of aiortc's key goals. Prefer plain, idiomatic Python
over compact or clever code. When a performance or memory argument exists,
measure it and present the trade-off rather than assuming it wins.

Avoid duplicating code. Before adding a helper, look for an existing one
that can be reused or extended, and factor out logic that would otherwise
be copied.

Use f-strings in new code. Logger calls are the exception: they keep lazy
`%` arguments, using the class's `__log_debug` helper where one exists.

In the library code, insert new functions and methods in alphabetical
order rather than appending them. Do not reorder existing code unless
that is the purpose of the change.

## Comments

Write comments as sentences: start with a capital letter and end with a
full stop. Do not reformat existing comments unless that is the purpose of
the change.

Keep comments short, typically one or two lines. State the non-obvious
reason and cite the specification section in parentheses (e.g. "(RFC 9260
section 5.2.1)"); longer quotes belong in the commit message.

## Tests

Code contributions must come with full unit test coverage. WebRTC is a
complex protocol stack, and ensuring correct behaviour now and in the
future requires a proper investment in automated testing.

Peer connection and ICE tests exchange packets over local sockets. Tests of
the layers above ICE, such as DTLS, SCTP and RTP, should instead use the
helpers in `tests/utils.py`:

- `@asynctest` to run a coroutine test method;
- `dummy_ice_transport_pair()` to connect two endpoints in memory, or
  `async with dummy_dtls_transport_pair()` for two endpoints that have
  completed the DTLS handshake;
- `set_loss_pattern()` to simulate packet loss;
- `load()` to read a captured packet from a `tests/*.bin` file, for parser
  tests.

## Checks

Before submitting, run the checks that continuous integration runs, from
the repository root:

```
coverage run -m unittest discover -v
coverage report -m
ruff check
ruff format --check
mypy
check-manifest
```

## Commit messages

Keep commit messages concise. The subject line states what the commit does
in the imperative; the body explains *why*: the goal being pursued, the
constraint that forced the approach, the non-obvious insight behind the
change. Skip anything a reader can trivially deduce from the diff: do not
enumerate the modified files, restate renamed symbols, or narrate the
changes function by function.

Quote a specification only where its wording matters, such as the MUST or
SHOULD that justifies a behaviour change, and keep the quote to the
relevant sentence. When the fix is obvious from the specification, citing
the section is enough.

Reference GitHub issues in a trailer at the end of the message, not inline
in the body. Use `Fixes: #N` only when the commit fully resolves the
issue, otherwise `See: #N`.

The changelog (`docs/changelog.rst`) is written at release time, so do not
update it in your pull request.
