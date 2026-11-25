"""Client de l'API turf.bzh (source principale).

Documentation : https://www.turf.bzh/api-docs.php
- URL de base : https://www.turf.bzh/api/v1
- Authentification : en-tete `Authorization: Bearer tbz_live_...`
- Reponse succes : {"data": ..., "meta": {...}} ; erreur : {"error": {...}}
- Limites (doublees jusqu'au 31/12/2026) : 120 req/min, 20 000 req/jour par cle.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from ..config import Settings
from .http_cache import HttpCache

# Libelles de discipline acceptes par les endpoints "journee"
DISCIPLINE_ATTELE = "trot attelé"
DISCIPLINE_MONTE = "trot monté"
DISCIPLINE_CODE_ATTELE = "A"
DISCIPLINE_CODE_MONTE = "M"


class TurfBzhError(RuntimeError):
    """Erreur renvoyee par l'API turf.bzh (bloc `error`) ou reseau."""

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class TurfBzhClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.has_turfbzh_key:
            raise TurfBzhError(
                "Cle API turf.bzh absente ou invalide. "
                "Renseigne TURFBZH_API_KEY dans le fichier .env "
                "(format tbz_live_...)."
            )
        self.settings = settings
        self.base = settings.turfbzh_base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {settings.turfbzh_api_key}",
                "Accept": "application/json",
                "User-Agent": "HippiquePredictor/0.1 (+usage personnel)",
            }
        )
        self.cache = HttpCache(settings.cache_dir, "turfbzh")
        self._last_call = 0.0
        # turf.bzh limite a 120 req/min : on vise <=110/min pour garder une marge.
        self._min_interval = max(settings.request_min_interval_s, 0.55)
        # Infos de quota lues dans la derniere reponse (meta.quota / en-tetes)
        self.last_quota: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Coeur HTTP
    # ------------------------------------------------------------------ #
    def _throttle(self) -> None:
        delta = time.time() - self._last_call
        wait = self._min_interval - delta
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_age_s: float | None = None,
        force_refresh: bool = False,
    ) -> Any:
        """GET sur l'API, avec cache disque optionnel.

        max_age_s=None signifie "cache valable indefiniment" (adapte aux
        courses passees). Pour la journee en cours, passer un max_age_s court.
        """
        url = f"{self.base}/{path.lstrip('/')}"
        params = {k: v for k, v in (params or {}).items() if v is not None}

        if not force_refresh:
            cached = self.cache.get(url, params, max_age_s)
            if cached is not None:
                return cached

        max_retries = 3
        for attempt in range(max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(
                    url, params=params, timeout=self.settings.http_timeout_s
                )
            except requests.RequestException as exc:
                raise TurfBzhError(f"Erreur reseau sur {url}: {exc}") from exc

            self._read_quota(resp)

            # Limite de debit : on respecte Retry-After et on reessaie.
            if resp.status_code == 429 and attempt < max_retries:
                time.sleep(self._retry_after(resp))
                continue

            try:
                body = resp.json()
            except ValueError:
                raise TurfBzhError(
                    f"Reponse non-JSON ({resp.status_code}) sur {url}: "
                    f"{resp.text[:200]}",
                    status=resp.status_code,
                )

            if resp.status_code != 200 or (isinstance(body, dict) and "error" in body):
                err = body.get("error", {}) if isinstance(body, dict) else {}
                raise TurfBzhError(
                    err.get("message") or f"HTTP {resp.status_code} sur {url}",
                    code=err.get("code") or err.get("reason"),
                    status=resp.status_code,
                )

            data = body.get("data") if isinstance(body, dict) else body
            self.cache.set(url, params, data)
            return data
        raise TurfBzhError(f"Limite de debit persistante sur {url}", status=429)

    @staticmethod
    def _retry_after(resp: requests.Response) -> float:
        hdr = resp.headers.get("Retry-After")
        if hdr:
            try:
                return min(30.0, float(hdr)) + 0.2
            except ValueError:
                pass
        try:
            err = resp.json().get("error", {})
            ra = err.get("retry_after")
            if ra:
                return min(30.0, float(ra)) + 0.2
        except (ValueError, AttributeError):
            pass
        return 2.0

    def _read_quota(self, resp: requests.Response) -> None:
        q: dict[str, Any] = {}
        for h in (
            "X-RateLimit-Limit-Minute",
            "X-RateLimit-Remaining-Minute",
            "X-RateLimit-Limit-Day",
            "X-RateLimit-Remaining-Day",
        ):
            if h in resp.headers:
                q[h] = resp.headers[h]
        if q:
            self.last_quota = q

    # ------------------------------------------------------------------ #
    # Endpoints : compte / sante
    # ------------------------------------------------------------------ #
    def me(self) -> Any:
        """Etat du compte : licence, abonnement, cle, credits, quota."""
        return self._get("me", max_age_s=0, force_refresh=True)

    def statut(self) -> Any:
        """Fraicheur de la base (fresh / stale / empty)."""
        return self._get("statut", max_age_s=60)

    # ------------------------------------------------------------------ #
    # Endpoints : programme / journee
    # ------------------------------------------------------------------ #
    def programme(self, date: str | None = None, filtre: str = "all") -> Any:
        """Programme d'une journee, une ligne par course."""
        return self._get(
            "programme",
            {"date": date, "filter": filtre},
            max_age_s=300,
        )

    def journee_partants(
        self,
        date: str,
        discipline: str | None = None,
        champs: str = "tout",
    ) -> Any:
        """Tous les partants d'une journee en un appel."""
        return self._get(
            f"journees/{date}/partants",
            {"discipline": discipline, "champs": champs},
            max_age_s=300,
        )

    def journee_cotes(
        self,
        date: str,
        discipline: str | None = None,
        vue: str = "partants",
        courses: str = "toutes",
    ) -> Any:
        """Cotes en direct de toutes les courses d'une journee."""
        return self._get(
            f"journees/{date}/cotes",
            {"discipline": discipline, "vue": vue, "courses": courses},
            max_age_s=60,
            force_refresh=True,
        )

    def journee_d4(self, date: str) -> Any:
        """Statut D4 (deferre des 4 pieds) des chevaux de trot de la journee."""
        return self._get(f"journees/{date}/d4", max_age_s=300)

    def resultats(self, date: str | None = None, code_course: str | None = None) -> Any:
        """Arrivees et rapports des courses terminees d'une journee."""
        return self._get(
            "resultats", {"date": date, "code_course": code_course}, max_age_s=120
        )

    # ------------------------------------------------------------------ #
    # Endpoints : une course
    # ------------------------------------------------------------------ #
    def course(
        self,
        date: str,
        rc: str,
        champs: str = "tout",
        hippodrome: str | None = None,
    ) -> Any:
        """Fiche d'une course + partants exacts (tous indicateurs)."""
        return self._get(
            f"courses/{date}/{rc}",
            {"champs": champs, "hippodrome": hippodrome},
            max_age_s=300,
        )

    def course_dossier(
        self,
        date: str,
        rc: str,
        inclure: str | None = None,
        historique: int | None = None,
        hippodrome: str | None = None,
    ) -> Any:
        """Tout le dossier d'une course en un appel (partants, indicateurs,
        classement, LigneBZH, ecarts, renifleur, ecuriebzh, cotes, mouvements,
        rapports, historique recent)."""
        return self._get(
            f"courses/{date}/{rc}/dossier",
            {"inclure": inclure, "historique": historique, "hippodrome": hippodrome},
            max_age_s=180,
        )

    def course_indicateurs(self, date: str, rc: str, champs: str = "tout") -> Any:
        return self._get(
            f"courses/{date}/{rc}/indicateurs", {"champs": champs}, max_age_s=300
        )

    def course_cotes(self, date: str, rc: str) -> Any:
        """Cotes PMU Simple Gagnant en direct d'une course."""
        return self._get(
            f"courses/{date}/{rc}/cotes", max_age_s=30, force_refresh=True
        )

    def course_cotes_mouvements(
        self, date: str, rc: str, fenetre: int = 5, limit: int = 8
    ) -> Any:
        """Les plus gros mouvements de cote sur une fenetre glissante."""
        return self._get(
            f"courses/{date}/{rc}/cotes/mouvements",
            {"fenetre": fenetre, "limit": limit},
            max_age_s=30,
            force_refresh=True,
        )

    def course_cotes_historique(self, date: str, rc: str) -> Any:
        """Serie complete des releves de cote d'une course."""
        return self._get(
            f"courses/{date}/{rc}/cotes/historique", max_age_s=120
        )

    def course_ecarts(self, date: str, rc: str) -> Any:
        """Ecarts de victoires / places (du cheval et du numero)."""
        return self._get(f"courses/{date}/{rc}/ecarts", max_age_s=600)

    def course_renifleur(self, date: str, rc: str) -> Any:
        """Carnet du Renifleur (resume + chevaux deja reperes)."""
        return self._get(f"courses/{date}/{rc}/renifleur", max_age_s=600)

    # ------------------------------------------------------------------ #
    # Endpoints : cheval / personnes
    # ------------------------------------------------------------------ #
    def cheval_historique(
        self, idcheval: int, discipline: str | None = None
    ) -> Any:
        """Les dernieres courses du cheval (max 50)."""
        return self._get(
            f"chevaux/{idcheval}/historique",
            {"discipline": discipline},
            max_age_s=3600,
        )

    def cheval_stats(self, idcheval: int, periode_jours: int | None = None) -> Any:
        return self._get(
            f"chevaux/{idcheval}/stats",
            {"periode_jours": periode_jours},
            max_age_s=3600,
        )

    def personne_stats(
        self,
        type_personne: str,
        id_personne: int,
        periode_jours: int | None = None,
        discipline: str | None = None,
        hippodrome: str | None = None,
    ) -> Any:
        """Stats d'un jockey/driver, entraineur, proprietaire ou eleveur."""
        return self._get(
            f"personnes/{type_personne}/{id_personne}/stats",
            {
                "periode_jours": periode_jours,
                "discipline": discipline,
                "hippodrome": hippodrome,
            },
            max_age_s=3600,
        )

    def schema(self) -> Any:
        """Dictionnaire des champs servis + taux de remplissage reel."""
        return self._get("schema", max_age_s=86400)
