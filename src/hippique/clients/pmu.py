"""Client de l'API PMU non officielle (2e source, usage personnel).

Base : https://online.turfinfo.api.pmu.fr/rest/client/1
Endpoints utilises :
  /programme/{DDMMYYYY}                      -> reunions + courses du jour
  /programme/{DDMMYYYY}/R{r}/C{c}            -> fiche d'une course
  /programme/{DDMMYYYY}/R{r}/C{c}/participants -> partants detailles

Apports par rapport a turf.bzh (pour le trot) :
  - conditions de course : sens de corde, type de piste, type de depart
  - par cheval : ferrure (`deferre`), oeilleres, gains detailles,
    `reductionKilometrique` et `commentaireApresCourse` sur les courses passees,
    cote + tendance (`dernierRapportDirect` / `dernierRapportReference`).

ATTENTION : API non documentee, susceptible de changer sans preavis. Elle est
utilisee ici pour un usage personnel, en complement de turf.bzh.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from ..config import Settings
from .http_cache import HttpCache

PMU_BASE = "https://online.turfinfo.api.pmu.fr/rest/client/1"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_RC_RE = re.compile(r"R(\d+)C(\d+)", re.I)


class PmuError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Normalisation des valeurs PMU
# --------------------------------------------------------------------------- #
def iso_to_pmu_date(date_iso: str) -> str:
    """'2026-11-23' -> '23112026' (format attendu par l'API PMU)."""
    y, m, d = date_iso.split("-")
    return f"{d}{m}{y}"


def centimes_to_euros(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v) / 100.0, 2)
    except (TypeError, ValueError):
        return None


def ms_to_seconds(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v) / 1000.0, 2)
    except (TypeError, ValueError):
        return None


def normalize_deferre(deferre: str | None) -> str:
    """Normalise le champ PMU `deferre` vers le vocabulaire du brief.

    Retours : 'D4' (4 pieds), 'DA' (anterieurs), 'DP' (posterieurs),
    'PLAQUE' (protege), 'FERRE' (aucune mention) ou valeur brute.
    """
    if not deferre:
        return "FERRE"
    s = deferre.upper()
    has_ant_def = "DEFERRE_ANTERIEURS" in s or s.startswith("DEFERRE_ANTERIEURS")
    has_post_def = "DEFERRE_POSTERIEURS" in s or "DEFERRRE_POSTERIEURS" in s
    if "DEFERRE_ANTERIEURS_POSTERIEURS" in s:
        return "D4"
    if has_ant_def and has_post_def:
        return "D4"
    if has_ant_def:
        return "DA"
    if has_post_def:
        return "DP"
    if "PROTEGE" in s:
        return "PLAQUE"
    return deferre


def parse_depart_type(conditions: str | None, parcours: str | None) -> dict[str, Any]:
    """Deduit le type de depart depuis le texte des conditions.

    Retourne {type: 'AUTOSTART'|'VOLTE'|None, infere: bool}.
    'autostart' est explicite dans le texte quand c'est le cas ; a defaut, au
    trot attele francais le depart est le plus souvent volte -> on le propose
    mais en marquant `infere=True`.
    """
    text = f"{conditions or ''} {parcours or ''}".lower()
    if re.search(r"auto\s*-?\s*start|autostart|élastique|elastique", text):
        return {"type": "AUTOSTART", "infere": False}
    if "volt" in text:
        return {"type": "VOLTE", "infere": False}
    return {"type": "VOLTE", "infere": True}


# --------------------------------------------------------------------------- #
# Structures
# --------------------------------------------------------------------------- #
@dataclass
class PmuCourse:
    code: str  # "R1C4" (numeros publics)
    reunion_path: int  # numero a utiliser dans l'URL
    course_path: int
    pays: str | None = None
    hippodrome: str | None = None
    specialite: str | None = None
    discipline: str | None = None
    distance: int | None = None
    corde: str | None = None
    parcours: str | None = None
    depart: dict[str, Any] = field(default_factory=dict)
    heure_ms: int | None = None
    allocation: int | None = None
    allocations_rang: dict[str, int] = field(default_factory=dict)
    conditions: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class PmuClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _UA, "Accept": "application/json"})
        self.cache = HttpCache(settings.cache_dir, "pmu")
        self._last = 0.0

    def _throttle(self) -> None:
        wait = self.settings.request_min_interval_s - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _get(self, path: str, *, max_age_s: float | None) -> Any:
        url = f"{PMU_BASE}/{path.lstrip('/')}"
        cached = self.cache.get(url, None, max_age_s)
        if cached is not None:
            return cached
        self._throttle()
        try:
            resp = self.session.get(url, timeout=self.settings.http_timeout_s)
        except requests.RequestException as exc:
            raise PmuError(f"Erreur reseau PMU: {exc}") from exc
        if resp.status_code == 404:
            raise PmuError(f"PMU 404: {url}")
        if resp.status_code != 200:
            raise PmuError(f"PMU HTTP {resp.status_code}: {url}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise PmuError(f"PMU reponse non-JSON: {url}") from exc
        self.cache.set(url, None, data)
        return data

    # ------------------------------------------------------------------ #
    def programme(self, date_iso: str) -> list[PmuCourse]:
        """Index des courses du jour (toutes disciplines)."""
        d = iso_to_pmu_date(date_iso)
        # journee en cours -> cache court ; journee passee -> cache long
        raw = self._get(f"programme/{d}", max_age_s=300)
        reunions = raw.get("programme", {}).get("reunions", [])
        courses: list[PmuCourse] = []
        for reu in reunions:
            # numOfficiel / numOrdre = numeros publics (R2, C3...), identiques a
            # ceux de turf.bzh et au chemin de l'URL PMU.
            r_num = reu.get("numOfficiel") or reu.get("numExterne")
            pays = (reu.get("pays") or {}).get("code")
            hippo = (reu.get("hippodrome") or {}).get("libelleCourt")
            for co in reu.get("courses", []):
                c_num = co.get("numOrdre") or co.get("numExterne")
                courses.append(
                    PmuCourse(
                        code=f"R{r_num}C{c_num}",
                        reunion_path=int(r_num),
                        course_path=int(c_num),
                        pays=pays,
                        hippodrome=(co.get("hippodrome") or {}).get("libelleCourt")
                        or hippo,
                        specialite=co.get("specialite"),
                        discipline=co.get("discipline"),
                        distance=co.get("distance"),
                        corde=co.get("corde"),
                        parcours=co.get("parcours"),
                        depart=parse_depart_type(
                            co.get("conditions"), co.get("parcours")
                        ),
                        heure_ms=co.get("heureDepart"),
                        allocation=co.get("montantTotalOffert") or co.get("montantPrix"),
                        allocations_rang={
                            k: co[k]
                            for k in (
                                "montantOffert1er",
                                "montantOffert2eme",
                                "montantOffert3eme",
                                "montantOffert4eme",
                                "montantOffert5eme",
                            )
                            if co.get(k) is not None
                        },
                        conditions=co.get("conditions"),
                        raw=co,
                    )
                )
        return courses

    def find_course(self, date_iso: str, code: str) -> PmuCourse | None:
        """Retrouve une course du jour a partir d'un code 'R1C4'."""
        m = _RC_RE.search(code or "")
        if not m:
            return None
        want = f"R{int(m.group(1))}C{int(m.group(2))}"
        for c in self.programme(date_iso):
            if c.code == want:
                return c
        return None

    def participants(
        self, date_iso: str, reunion_path: int, course_path: int
    ) -> list[dict[str, Any]]:
        """Partants normalises d'une course (courses passees : resultats inclus)."""
        d = iso_to_pmu_date(date_iso)
        raw = self._get(
            f"programme/{d}/R{reunion_path}/C{course_path}/participants",
            max_age_s=120,
        )
        parts = raw.get("participants", raw if isinstance(raw, list) else [])
        return [self._normalize_participant(p) for p in parts]

    @staticmethod
    def _normalize_participant(p: dict[str, Any]) -> dict[str, Any]:
        gains = p.get("gainsParticipant") or {}
        direct = p.get("dernierRapportDirect") or {}
        ref = p.get("dernierRapportReference") or {}
        comment = p.get("commentaireApresCourse") or {}
        return {
            "num": p.get("numPmu"),
            "nom": p.get("nom"),
            "idcheval_pmu": p.get("idCheval"),
            "age": p.get("age"),
            "sexe": p.get("sexe"),
            "race": p.get("race"),
            "statut": p.get("statut"),  # PARTANT / NON_PARTANT
            "driver": p.get("driver"),
            "driver_change": p.get("driverChange"),
            "entraineur": p.get("entraineur"),
            "proprietaire": p.get("proprietaire"),
            "eleveur": p.get("eleveur"),
            "oeilleres": p.get("oeilleres"),
            "deferre_raw": p.get("deferre"),
            "ferrure": normalize_deferre(p.get("deferre")),
            "musique": p.get("musique"),
            "nombre_courses": p.get("nombreCourses"),
            "nombre_victoires": p.get("nombreVictoires"),
            "nombre_places": p.get("nombrePlaces"),
            "gains_carriere_eur": centimes_to_euros(gains.get("gainsCarriere")),
            "gains_victoires_eur": centimes_to_euros(gains.get("gainsVictoires")),
            "gains_annee_eur": centimes_to_euros(gains.get("gainsAnneeEnCours")),
            "handicap_distance": p.get("handicapDistance"),
            "allure": p.get("allure"),
            "avis_entraineur": p.get("avisEntraineur"),
            # resultats (courses passees)
            "rang_arrivee": p.get("ordreArrivee"),
            "temps_obtenu_s": ms_to_seconds(p.get("tempsObtenu")),
            "reduction_km_s": ms_to_seconds(p.get("reductionKilometrique")),
            "incident": p.get("incident"),
            "commentaire_course": comment.get("texte"),
            # marche
            "cote_direct": direct.get("rapport"),
            "cote_tendance": direct.get("nombreIndicateurTendance"),
            "cote_reference": ref.get("rapport"),
            "favori": direct.get("favoris"),
        }
