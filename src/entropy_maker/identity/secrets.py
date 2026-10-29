"""Load an API-wallet key from a mode-0600 file. Never from argv."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

_KEY = re.compile(r"^0x[0-9a-fA-F]{64}$")


class SecretError(RuntimeError):
    pass


def load_secret_file(path: Path) -> str:
    if path.is_symlink():
        raise SecretError(f"{path} is a symlink; refusing to read it")
    try:
        info = path.stat()
    except OSError as exc:
        raise SecretError(f"cannot stat {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise SecretError(f"{path} is not a regular file")
    if info.st_mode & 0o077:
        raise SecretError(f"{path} is group/world accessible; chmod 600 and try again")
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SecretError(f"cannot read {path}") from exc
    if not _KEY.fullmatch(text):
        raise SecretError(f"{path} does not contain a single 32-byte hex key")
    return text


def load_secret_value(value: str) -> str:
    text = value.strip()
    if not _KEY.fullmatch(text):
        raise SecretError("ENTROPY_AGENT_SECRET is not a 32-byte hex key")
    return text


def assert_env_file_has_no_key(text: str) -> None:
    if re.search(r"(?i)(?<![0-9a-f])0x[0-9a-f]{64}(?![0-9a-f])", text):
        raise SecretError("env file contains a private key; move it to the 0600 secret file")


def file_mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)
