"""Atomic JSON state. A crash during a write leaves the previous file in place."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from entropy_maker.market.rounding import decimal_from
from entropy_maker.models import FillRecord, RuntimeState, SideSlot


class StateError(RuntimeError):
    pass


def save_state(path: Path, state: RuntimeState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_encode(state), indent=2, sort_keys=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_state(path: Path) -> RuntimeState:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StateError(f"no state file at {path}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"state file {path} is not JSON") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise StateError("state file version is not 1")
    try:
        return RuntimeState(
            version=1,
            coin=str(raw["coin"]),
            mode=raw["mode"],
            position=decimal_from(raw["position"]),
            cash=decimal_from(raw["cash"]),
            bid=_slot(raw["bid"]),
            ask=_slot(raw["ask"]),
            uncertain=tuple(raw.get("uncertain") or ()),
            halted=bool(raw["halted"]),
            halt_reason=raw.get("halt_reason"),
            book_rx_ms=raw.get("book_rx_ms"),
            updated_ms=int(raw["updated_ms"]),
            seen_account=bool(raw.get("seen_account", False)),
            read_errors=int(raw.get("read_errors", 0)),
            book_errors=int(raw.get("book_errors", 0)),
            recent_fills=tuple(_fill(item) for item in raw.get("recent_fills") or ()),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StateError("state file is missing required fields") from exc


def _encode(state: RuntimeState) -> dict:
    raw = asdict(state)
    return _stringify(raw)


def _stringify(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _stringify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify(item) for item in value]
    return value


def _slot(raw: object) -> SideSlot:
    if not isinstance(raw, dict):
        raise StateError("slot is not an object")
    return SideSlot(
        phase=raw.get("phase", "empty"),
        cloid=raw.get("cloid"),
        px=_opt_decimal(raw.get("px")),
        orig_sz=_opt_decimal(raw.get("orig_sz")),
        remaining=_opt_decimal(raw.get("remaining")),
        placed_ms=raw.get("placed_ms"),
        cancel_sent=bool(raw.get("cancel_sent", False)),
        cancel_polls=int(raw.get("cancel_polls", 0)),
        oid=raw.get("oid"),
        reduce_only=bool(raw.get("reduce_only", False)),
        replace_sz=_opt_decimal(raw.get("replace_sz")),
    )


def _fill(raw: object) -> FillRecord:
    if not isinstance(raw, dict):
        raise StateError("fill is not an object")
    return FillRecord(
        cloid=str(raw["cloid"]),
        side=raw["side"],
        px=str(raw["px"]),
        sz=str(raw["sz"]),
        time_ms=int(raw["time_ms"]),
    )


def _opt_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return decimal_from(value)
