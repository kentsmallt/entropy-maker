"""Optional l2Book websocket. Quiet sockets age out and the quote gate pulls orders."""

from __future__ import annotations

import json
import threading
import time
from urllib.parse import urlparse

from entropy_maker.exchange.info_client import InfoError
from entropy_maker.market.book import BookError, parse_book
from entropy_maker.models import Book


def ws_url_for(info_url: str) -> str:
    parsed = urlparse(info_url)
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    return f"{scheme}://{parsed.netloc}/ws"


def book_from_ws_message(message: str, coin: str) -> Book | None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("channel") != "l2Book":
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    try:
        return parse_book(data, coin)
    except BookError:
        return None


class WsBookFeed:
    """Background subscriber. ``snapshot`` raises until the first book arrives."""

    def __init__(self, info_url: str, coin: str) -> None:
        try:
            import websocket
        except ImportError as exc:
            raise InfoError('ENTROPY_BOOK_SOURCE=ws needs pip install -e ".[ws]"') from exc
        self._ws_module = websocket
        self._url = ws_url_for(info_url)
        self._coin = coin
        self._lock = threading.Lock()
        self._book: Book | None = None
        self._received_ms: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="entropy-maker-book", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def snapshot(self) -> Book:
        with self._lock:
            book = self._book
        if book is None:
            raise InfoError("websocket book has not arrived")
        return book

    def received_ms(self) -> int | None:
        with self._lock:
            return self._received_ms

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                socket = self._ws_module.create_connection(self._url, timeout=10)
            except Exception:
                if self._stop.wait(2):
                    return
                continue
            try:
                socket.settimeout(5)
                socket.send(
                    json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": self._coin}})
                )
                while not self._stop.is_set():
                    try:
                        raw = socket.recv()
                    except Exception:
                        break
                    if not isinstance(raw, str):
                        continue
                    book = book_from_ws_message(raw, self._coin)
                    if book is None:
                        continue
                    with self._lock:
                        self._book = book
                        self._received_ms = int(time.time() * 1000)
            finally:
                try:
                    socket.close()
                except Exception:
                    pass
            if not self._stop.is_set():
                self._stop.wait(1)
