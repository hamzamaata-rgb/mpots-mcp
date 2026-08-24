"""Client HTTP poli : robots.txt + rate limit par domaine + backoff + archivage brut."""
from __future__ import annotations

import gzip
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import SETTINGS
from .robots import RobotsGate


class PoliteClient:
    """Une requête à la fois, délai fixe + jitter par domaine, respect du
    Crawl-delay si plus grand, backoff exponentiel sur 429/5xx, jamais de
    contournement (pas de rotation d'UA/IP, pas de résolution de CAPTCHA)."""

    def __init__(self, delay: float | None = None, user_agent: str | None = None):
        self.delay = delay if delay is not None else SETTINGS.request_delay_seconds
        self.user_agent = user_agent or SETTINGS.user_agent
        self.gate = RobotsGate(self.user_agent)
        self._last_request: dict[str, float] = {}
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Language": "fr,ar;q=0.8,en;q=0.6"},
            timeout=SETTINGS.request_timeout_seconds,
            follow_redirects=True,
        )
        self.stats = {"fetched": 0, "blocked_robots": 0, "errors": 0}

    def _wait(self, host: str, url: str) -> None:
        crawl_delay = self.gate.crawl_delay(url) or 0
        delay = max(self.delay, crawl_delay) + random.uniform(0, 1.0)
        elapsed = time.monotonic() - self._last_request.get(host, 0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request[host] = time.monotonic()

    def get(self, url: str) -> httpx.Response | None:
        if not self.gate.allowed(url):
            self.stats["blocked_robots"] += 1
            return None
        host = urlparse(url).netloc
        backoff = 2.0
        for attempt in range(SETTINGS.max_retries + 1):
            self._wait(host, url)
            try:
                resp = self._client.get(url)
            except httpx.HTTPError:
                self.stats["errors"] += 1
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < SETTINGS.max_retries:
                retry_after = resp.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else backoff)
                backoff *= 2
                continue
            if resp.status_code == 200:
                self.stats["fetched"] += 1
                return resp
            self.stats["errors"] += 1
            return None
        return None

    def close(self) -> None:
        self._client.close()


class RawStore:
    """Archive brute append-only : un JSONL.gz par run.
    Chaque ligne : {'url', 'fetched_at', 'status', 'body'} — permet de
    re-parser sans re-scraper et de tracer chaque observation (raw_ref)."""

    def __init__(self, source_code: str, run_label: str | None = None):
        SETTINGS.ensure_dirs()
        label = run_label or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = SETTINGS.raw_dir / source_code
        directory.mkdir(parents=True, exist_ok=True)
        self.path: Path = directory / f"{label}.jsonl.gz"
        self._fh = gzip.open(self.path, "at", encoding="utf-8")
        self._line = 0

    def write(self, url: str, body: str, status: int = 200, meta: dict | None = None) -> str:
        entry = {
            "url": url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "meta": meta or {},
            "body": body,
        }
        self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._line += 1
        return f"{self.path}#{self._line}"

    def close(self) -> None:
        self._fh.close()
