"""Configuration centrale du projet (lue depuis l'environnement / .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv est dans requirements.txt, mais on reste tolerant
    load_dotenv = None


# Racine du depot (…/HippiquePredictor)
ROOT_DIR = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    """Charge le fichier .env a la racine du projet, s'il existe."""
    if load_dotenv is not None:
        env_path = ROOT_DIR / ".env"
        if env_path.exists():
            load_dotenv(env_path)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "oui", "on"}


@dataclass(frozen=True)
class Settings:
    """Parametres resolus de l'application."""

    turfbzh_api_key: str | None
    turfbzh_base_url: str = "https://www.turf.bzh/api/v1"
    pmu_enabled: bool = True
    letrot_enabled: bool = True
    cache_dir: Path = ROOT_DIR / ".cache"
    out_dir: Path = ROOT_DIR / "out"
    # Delai minimal entre deux appels a une meme source (politesse / anti-ban)
    request_min_interval_s: float = 0.4
    http_timeout_s: float = 20.0

    @property
    def has_turfbzh_key(self) -> bool:
        key = self.turfbzh_api_key
        return bool(key) and key.startswith("tbz_") and "REMPLACE" not in key


def load_settings() -> Settings:
    """Construit les parametres a partir de l'environnement."""
    _load_env()

    cache_dir = os.getenv("HIPPIQUE_CACHE_DIR")
    cache_path = Path(cache_dir) if cache_dir else (ROOT_DIR / ".cache")
    if not cache_path.is_absolute():
        cache_path = ROOT_DIR / cache_path

    return Settings(
        turfbzh_api_key=os.getenv("TURFBZH_API_KEY"),
        turfbzh_base_url=os.getenv("TURFBZH_BASE_URL", "https://www.turf.bzh/api/v1"),
        pmu_enabled=_as_bool(os.getenv("PMU_ENABLED"), default=True),
        letrot_enabled=_as_bool(os.getenv("LETROT_ENABLED"), default=True),
        cache_dir=cache_path,
        out_dir=ROOT_DIR / "out",
    )


# Instance partagee
SETTINGS = load_settings()
