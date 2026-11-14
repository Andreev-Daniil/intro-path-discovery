"""HTTP-слой: лимит частоты, повторы, дисковый кэш.

Все внешние запросы идут через `HttpClient`, поэтому в тестах его заменяют
объектом с теми же методами `get_json` / `get_text`.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol

import requests


class Http(Protocol):
    def get_json(self, url: str, params: dict | None = None) -> Any: ...
    def get_text(self, url: str, params: dict | None = None) -> str: ...


class HttpError(RuntimeError):
    pass


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        min_interval: float = 0.15,
        cache_dir: Path | None = None,
        cache_ttl: float = 24 * 3600,
        retries: int = 3,
        timeout: float = 30,
        sleep=time.sleep,
        session: requests.Session | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.min_interval = min_interval
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl
        self.retries = retries
        self.timeout = timeout
        self._sleep = sleep
        self._last = 0.0

    def _cache_path(self, url: str, params: dict | None) -> Path | None:
        if not self.cache_dir:
            return None
        key = json.dumps([url, sorted((params or {}).items())], ensure_ascii=False)
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".txt")

    def get_text(self, url: str, params: dict | None = None) -> str:
        cache = self._cache_path(url, params)
        if cache and cache.exists() and time.time() - cache.stat().st_mtime < self.cache_ttl:
            return cache.read_text(encoding="utf-8")

        last_error = ""
        for attempt in range(self.retries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = str(exc)
            else:
                if resp.status_code == 200:
                    if cache:
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        cache.write_text(resp.text, encoding="utf-8")
                    return resp.text
                if resp.status_code not in (429, 500, 502, 503, 504):
                    raise HttpError(f"{resp.status_code} для {url}")
                last_error = f"{resp.status_code}"
            self._sleep(2 ** attempt)
        raise HttpError(f"не удалось получить {url}: {last_error}")

    def get_json(self, url: str, params: dict | None = None) -> Any:
        return json.loads(self.get_text(url, params))
