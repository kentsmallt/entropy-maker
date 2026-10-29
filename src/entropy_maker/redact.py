"""Log redaction. Private keys are 32 bytes; cloids and addresses are shorter."""

from __future__ import annotations

import re

# 32-byte hex (private keys, and also tx hashes). Cloids are 16 bytes and
# addresses are 20 bytes, so they are left visible.
_SECRET = re.compile(r"(?i)(?<![0-9a-f])0x[0-9a-f]{64}(?![0-9a-f])")
_BARE_SECRET = re.compile(r"(?i)(?<![0-9a-f])(?<!0x)[0-9a-f]{64}(?![0-9a-f])")


def redact(text: str) -> str:
    cleaned = _SECRET.sub("0x[REDACTED]", text)
    return _BARE_SECRET.sub("[REDACTED]", cleaned)
