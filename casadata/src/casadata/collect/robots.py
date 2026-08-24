"""Conformité robots.txt — vérifiée À L'EXÉCUTION, jamais supposée.

Chaque URL passe par RobotsGate.allowed() avant toute requête. En cas de
robots.txt inaccessible, on applique la politique conservatrice configurée
(par défaut : refuser le crawl du domaine et le signaler).
"""
from __future__ import annotations

import urllib.robotparser
from urllib.parse import urlparse

import httpx


class RobotsGate:
    def __init__(self, user_agent: str, fail_open: bool = False, timeout: float = 15.0):
        self.user_agent = user_agent
        self.fail_open = fail_open   # False = en cas de doute, on ne crawle pas
        self.timeout = timeout
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.blocked_log: list[str] = []

    def _parser_for(self, url: str):
        host = urlparse(url).netloc
        if host in self._parsers:
            return self._parsers[host]
        robots_url = f"https://{host}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        try:
            resp = httpx.get(robots_url, timeout=self.timeout,
                             headers={"User-Agent": self.user_agent},
                             follow_redirects=True)
            if resp.status_code in (401, 403):
                # le site refuse l'accès au robots.txt -> considérer tout interdit
                parser.disallow_all = True
            elif resp.status_code >= 400:
                # pas de robots.txt -> tout autorisé (standard)
                parser.allow_all = True
            else:
                parser.parse(resp.text.splitlines())
        except Exception:
            parser = None if not self.fail_open else parser
            if parser is not None:
                parser.allow_all = True
        self._parsers[host] = parser
        return parser

    def allowed(self, url: str) -> bool:
        parser = self._parser_for(url)
        if parser is None:
            self.blocked_log.append(f"robots.txt inaccessible, skip: {url}")
            return False
        ok = parser.can_fetch(self.user_agent, url) and parser.can_fetch("*", url)
        if not ok:
            self.blocked_log.append(f"disallow robots.txt: {url}")
        return ok

    def crawl_delay(self, url: str) -> float | None:
        parser = self._parser_for(url)
        if parser is None:
            return None
        try:
            return parser.crawl_delay(self.user_agent) or parser.crawl_delay("*")
        except Exception:
            return None
