"""Fonctions utilitaires partagees (dates, musique, ferrure, discipline)."""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def today_iso() -> str:
    return _dt.date.today().isoformat()


def parse_iso(date_iso: str) -> _dt.date:
    return _dt.date.fromisoformat(date_iso)


def days_between(d1_iso: str, d2_iso: str) -> int | None:
    try:
        return abs((parse_iso(d1_iso) - parse_iso(d2_iso)).days)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Discipline
# --------------------------------------------------------------------------- #
def is_attele(discipline: Any) -> bool:
    s = str(discipline or "").strip().lower()
    return s == "a" or "attel" in s


def is_monte(discipline: Any) -> bool:
    s = str(discipline or "").strip().lower()
    return s == "m" or "mont" in s


# --------------------------------------------------------------------------- #
# Ferrure (vocabulaire PMU / turf.bzh -> categories du brief)
# --------------------------------------------------------------------------- #
def normalize_ferrure(ferrure: str | None) -> str:
    """-> 'D4' | 'DA' | 'DP' | 'PLAQUE' | 'FERRE' (ou valeur brute)."""
    if not ferrure:
        return "FERRE"
    s = ferrure.upper()
    ant_def = "DEFERRE_ANTERIEURS" in s
    post_def = "DEFERRE_POSTERIEURS" in s or "DEFERRRE_POSTERIEURS" in s
    if "DEFERRE_ANTERIEURS_POSTERIEURS" in s or (ant_def and post_def):
        return "D4"
    if ant_def:
        return "DA"
    if post_def:
        return "DP"
    if "PROTEGE" in s or "PLAQUE" in s:
        return "PLAQUE"
    return ferrure


FERRURE_LABELS = {
    "D4": "deferre des 4 pieds",
    "DA": "deferre anterieurs",
    "DP": "deferre posterieurs",
    "PLAQUE": "protege / plaque",
    "FERRE": "ferre",
}


# --------------------------------------------------------------------------- #
# Musique (ex : "Da 1a 3a 0a 5m Dm (23) 2a")
# --------------------------------------------------------------------------- #
_MUSIQUE_TOKEN = re.compile(r"(\d+|[DdAaTtRrNn])\s*([ampshcAMPSHC])?")


def parse_musique(musique: str | None) -> list[dict[str, Any]]:
    """Decoupe une musique en performances {place, code, discipline, fault, disq}.

    - place : entier (0 = non place / au-dela), None si lettre.
    - code : le jeton brut ('1', 'D', 'A', 'T', ...).
    - discipline : 'a' (attele), 'm' (monte), 'p', etc. ou None.
    - fault : True pour disqualification/arret/chute (D, A, T, R).
    """
    if not musique:
        return []
    out: list[dict[str, Any]] = []
    # on retire les annees entre parentheses, ex "(23)"
    cleaned = re.sub(r"\(\d+\)", " ", musique)
    for m in _MUSIQUE_TOKEN.finditer(cleaned):
        tok, disc = m.group(1), m.group(2)
        is_num = tok.isdigit()
        letter = None if is_num else tok.upper()
        out.append(
            {
                "place": int(tok) if is_num else None,
                "code": tok.upper(),
                "discipline": disc.lower() if disc else None,
                "fault": letter in {"D", "A", "T", "R"},
                "disq": letter == "D",
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Rang d'arrivee (champ Rank / Place_Precedente : "1".."n" ou lettre)
# --------------------------------------------------------------------------- #
def parse_rank(rank: Any) -> dict[str, Any]:
    """-> {place:int|None, fault:bool, np:bool, raw:str}."""
    s = str(rank).strip().upper() if rank is not None else ""
    if s.isdigit():
        return {"place": int(s), "fault": False, "np": False, "raw": s}
    return {
        "place": None,
        "fault": s in {"D", "A", "T", "R"},
        "np": s == "NP",
        "raw": s,
    }


# --------------------------------------------------------------------------- #
# Reduction kilometrique : conversions d'affichage
# --------------------------------------------------------------------------- #
def rk_to_str(rk_seconds: float | None) -> str | None:
    """75.7 -> "1'15\"7" (format turfiste)."""
    if rk_seconds is None:
        return None
    minutes = int(rk_seconds // 60)
    rest = rk_seconds - minutes * 60
    sec = int(rest)
    tenths = round((rest - sec) * 10)
    if tenths == 10:
        sec += 1
        tenths = 0
    return f"{minutes}'{sec:02d}\"{tenths}"


# --------------------------------------------------------------------------- #
# Petites stats robustes
# --------------------------------------------------------------------------- #
def median(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def norm_rank(value: float | None, values: list[float | None], lower_better: bool) -> float | None:
    """Position comparative de `value` dans le lot, en [0,1] (1 = meilleur).

    Base sur le rang (robuste aux valeurs extremes). None si non evaluable.
    """
    vals = [v for v in values if v is not None]
    if value is None or len(vals) < 2:
        return None
    if lower_better:
        worse = sum(1 for v in vals if v > value)
    else:
        worse = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return clamp((worse + 0.5 * max(0, equal - 1)) / (len(vals) - 1))


def safe_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def safe_int(v: Any) -> int | None:
    f = safe_float(v)
    return int(f) if f is not None else None


def norm_name(s: str | None) -> str:
    """Normalise un libelle pour comparaison (minuscules, alphanum only)."""
    if not s:
        return ""
    return "".join(ch for ch in s.lower() if ch.isalnum())
