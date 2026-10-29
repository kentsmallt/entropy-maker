"""128-bit client order ids with a fixed maker tag in the first four bytes.

The tag is ASCII ``EMM1`` (``0x454d4d31``). Orders without it are treated as
foreign and are never canceled by this process.
"""

from __future__ import annotations

import secrets

TAG = "454d4d31"
TAG_BYTES = bytes.fromhex(TAG)


class CloidError(ValueError):
    pass


def is_ours(cloid: str | None) -> bool:
    if not cloid or not isinstance(cloid, str):
        return False
    body = cloid[2:] if cloid.startswith(("0x", "0X")) else cloid
    if len(body) != 32:
        return False
    try:
        bytes.fromhex(body)
    except ValueError:
        return False
    return body.lower().startswith(TAG)


def normalize(cloid: str) -> str:
    if not is_ours(cloid):
        raise CloidError("cloid is missing the entropy-maker tag or is not 16 bytes")
    body = cloid[2:] if cloid.startswith(("0x", "0X")) else cloid
    return "0x" + body.lower()


def new_cloid() -> str:
    raw = TAG_BYTES + secrets.token_bytes(12)
    return "0x" + raw.hex()


def validate_any(cloid: str) -> str:
    """Validate a 16-byte cloid that may belong to someone else."""
    if not isinstance(cloid, str):
        raise CloidError("cloid is not a string")
    body = cloid[2:] if cloid.startswith(("0x", "0X")) else cloid
    if len(body) != 32:
        raise CloidError("cloid is not 16 bytes")
    try:
        bytes.fromhex(body)
    except ValueError as exc:
        raise CloidError("cloid is not hex") from exc
    return "0x" + body.lower()
