"""Verification rapide de la connexion a l'API turf.bzh.

Usage :
    python check_api.py            # etat du compte + programme trot attele du jour
    python check_api.py 2026-11-23 # idem pour une date donnee
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permet de lancer le script sans installer le paquet
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from hippique.config import load_settings  # noqa: E402
from hippique.clients.turfbzh import (  # noqa: E402
    DISCIPLINE_ATTELE,
    TurfBzhClient,
    TurfBzhError,
)


def main() -> int:
    date = sys.argv[1] if len(sys.argv) > 1 else None
    settings = load_settings()

    if not settings.has_turfbzh_key:
        print("[X] Cle API turf.bzh absente ou non renseignee.")
        print("    Ouvre le fichier .env et remplace la valeur de TURFBZH_API_KEY")
        print("    par ta cle (format tbz_live_...).")
        return 1

    try:
        client = TurfBzhClient(settings)
    except TurfBzhError as exc:
        print(f"[X] {exc}")
        return 1

    # 1) Etat du compte
    print("== /v1/me ==")
    try:
        me = client.me()
        _print_compact(me)
    except TurfBzhError as exc:
        print(f"[X] Echec /me : {exc} (code={exc.code}, status={exc.status})")
        return 1

    # 2) Programme du jour -> courses de trot attele
    print("\n== /v1/programme (trot attele) ==")
    try:
        prog = client.programme(date=date)
        courses = _extract_courses(prog)
        attele = [c for c in courses if _is_attele(c)]
        print(f"Total courses: {len(courses)} | trot attele: {len(attele)}")
        for c in attele[:40]:
            print(
                f"  {c.get('code_course','?'):7} "
                f"{str(c.get('hippodrome','?'))[:22]:22} "
                f"{str(c.get('heure','?')):5} "
                f"{c.get('distance','?')}m "
                f"{c.get('nombre_partants','?')} part."
            )
    except TurfBzhError as exc:
        print(f"[X] Echec /programme : {exc} (code={exc.code}, status={exc.status})")
        return 1

    if client.last_quota:
        print("\nQuota:", client.last_quota)
    print("\n[OK] Connexion API fonctionnelle.")
    return 0


def _print_compact(obj, indent: str = "  ") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                print(f"{indent}{k}:")
                _print_compact(v, indent + "  ")
            else:
                print(f"{indent}{k}: {v}")
    elif isinstance(obj, list):
        print(f"{indent}({len(obj)} elements)")
    else:
        print(f"{indent}{obj}")


def _extract_courses(prog) -> list:
    if isinstance(prog, list):
        return prog
    if isinstance(prog, dict):
        for key in ("courses", "programme", "data"):
            if isinstance(prog.get(key), list):
                return prog[key]
    return []


def _is_attele(course: dict) -> bool:
    disc = str(course.get("discipline", "")).strip()
    return disc.upper() == "A" or disc.lower() == DISCIPLINE_ATTELE


if __name__ == "__main__":
    raise SystemExit(main())
