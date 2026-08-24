"""Configuration centrale de casadata.

Tout est surchargable par variables d'environnement pour tourner en local,
sur VPS ou en CI sans modifier le code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _root() -> Path:
    env = os.environ.get("CASADATA_HOME")
    if env:
        return Path(env)
    return Path.cwd() / "data"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=_root)
    db_filename: str = "casadata.duckdb"
    # Politesse HTTP — valeurs volontairement conservatrices.
    request_delay_seconds: float = float(os.environ.get("CASADATA_DELAY", "4.0"))
    request_timeout_seconds: float = 30.0
    max_retries: int = 4
    user_agent: str = os.environ.get(
        "CASADATA_UA",
        "casadata-research-bot/0.1 (+contact: set CASADATA_CONTACT)",
    )
    contact: str = os.environ.get("CASADATA_CONTACT", "")
    # Sel pour le hachage des identifiants vendeurs (données personnelles minimisées).
    seller_hash_salt: str = os.environ.get("CASADATA_SALT", "casadata-default-salt")

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def incoming_dir(self) -> Path:
        return self.data_dir / "incoming"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "export"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.raw_dir, self.incoming_dir, self.export_dir):
            p.mkdir(parents=True, exist_ok=True)


SETTINGS = Settings()

# Sources déclarées. kind: portal|institutional|dataset|archive|aggregate|geo
KNOWN_SOURCES: list[dict] = [
    {"code": "mubawab", "name": "Mubawab.ma", "kind": "portal", "base_url": "https://www.mubawab.ma"},
    {"code": "avito", "name": "Avito.ma", "kind": "portal", "base_url": "https://www.avito.ma"},
    {"code": "sarouty", "name": "Sarouty.ma (Property Finder)", "kind": "portal", "base_url": "https://www.sarouty.ma"},
    {"code": "yakeey", "name": "Yakeey — référentiel de prix", "kind": "aggregate", "base_url": "https://yakeey.com"},
    {"code": "agenz", "name": "Agenz — référentiel de prix", "kind": "aggregate", "base_url": "https://agenz.ma"},
    {"code": "ipai", "name": "BKAM x ANCFCC — IPAI", "kind": "institutional", "base_url": "https://www.bkam.ma"},
    {"code": "hcp", "name": "Haut-Commissariat au Plan", "kind": "institutional", "base_url": "https://www.hcp.ma"},
    {"code": "wayback", "name": "Internet Archive Wayback Machine", "kind": "archive", "base_url": "https://web.archive.org"},
    {"code": "osm", "name": "OpenStreetMap", "kind": "geo", "base_url": "https://www.openstreetmap.org"},
    {"code": "seed_github_chp", "name": "Seed GitHub Casablanca-House-Prices (~2020)", "kind": "dataset"},
    {"code": "kaggle_ma_housing", "name": "Kaggle housing-data-in-morocco", "kind": "dataset"},
    {"code": "university_2019_2021", "name": "Dataset universitaire Casablanca 2019-2021 (Avito+Mubawab+Sarouty)", "kind": "dataset"},
    {"code": "generic_csv", "name": "Import CSV générique (manifest requis)", "kind": "dataset"},
]
