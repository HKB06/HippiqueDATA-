"""Petit cache disque pour les reponses HTTP GET (economise le quota API).

Les reponses sont stockees en JSON sous `cache_dir/<source>/<hash>.json`.
Le cache est volontairement simple : une entree par (methode, url, params).
Les donnees de courses passees ne changent plus ; pour la journee en cours,
utiliser `max_age_s` court ou `force_refresh`.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class HttpCache:
    def __init__(self, cache_dir: Path, namespace: str) -> None:
        self.dir = Path(cache_dir) / namespace
        self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, url: str, params: dict[str, Any] | None) -> str:
        raw = url + "?" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _path(self, url: str, params: dict[str, Any] | None) -> Path:
        return self.dir / f"{self._key(url, params)}.json"

    def get(
        self,
        url: str,
        params: dict[str, Any] | None,
        max_age_s: float | None,
    ) -> Any | None:
        """Retourne la valeur en cache si elle existe et n'est pas perimee."""
        path = self._path(url, params)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if max_age_s is not None:
            age = time.time() - payload.get("_cached_at", 0)
            if age > max_age_s:
                return None
        return payload.get("data")

    def set(self, url: str, params: dict[str, Any] | None, data: Any) -> None:
        path = self._path(url, params)
        payload = {"_cached_at": time.time(), "url": url, "params": params, "data": data}
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
