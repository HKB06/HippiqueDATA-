"""Interface en ligne de commande de HippiquePredictor.

Exemples :
    python main.py                      # tout le trot attele FR du jour
    python main.py --course R2C2        # une course precise (date du jour)
    python main.py --date 2026-11-25 --course R4C3
    python main.py --hippodrome vincennes
    python main.py --enrich             # enrichit l'historique (RK + commentaires PMU)
    python main.py --json out/analyse.json --prompt
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console

from . import helpers as H
from .clients.turfbzh import TurfBzhError
from .collect import Collector
from .config import load_settings
from .engine import V5Engine
from .output import (
    analysis_to_dict,
    build_prompt_bundle,
    render_analysis,
    save_json,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hippique",
        description="Analyse des courses de trot attele (methode V5).",
    )
    p.add_argument("--date", default=H.today_iso(), help="Date YYYY-MM-DD (defaut: aujourd'hui).")
    p.add_argument("--course", help="Code d'une course precise, ex R2C2.")
    p.add_argument("--hippodrome", help="Filtre hippodrome (recherche partielle).")
    p.add_argument(
        "--no-france-filter", action="store_true",
        help="Ne pas limiter au trot attele francais.",
    )
    p.add_argument(
        "--no-enrich", action="store_true",
        help="Desactiver l'enrichissement PMU (reduction km + commentaires).",
    )
    p.add_argument(
        "--no-actor-stats", action="store_true",
        help="Desactiver les stats driver/entraineur (turf.bzh /personnes).",
    )
    p.add_argument(
        "--enrich-depth", type=int, default=None,
        help="Nb de sorties enrichies par cheval (defaut smart: 6 en course unique, 3 en journee).",
    )
    p.add_argument(
        "--fast", action="store_true",
        help="Mode rapide : pas d'enrichissement ni de stats acteurs (survol d'une journee).",
    )
    p.add_argument("--json", metavar="PATH", help="Enregistrer le resultat JSON.")
    p.add_argument(
        "--prompt", action="store_true",
        help="Exporter aussi le prompt V5 + donnees pour une IA (dossier out/prompts).",
    )
    p.add_argument("--no-history", action="store_true", help="Ne pas charger l'historique (rapide).")
    return p


def _select_races(collector: Collector, args) -> list[str]:
    if args.course:
        return [args.course.upper()]
    races = collector.attele_races(args.date, france_only=not args.no_france_filter)
    codes = []
    for r in races:
        if args.hippodrome:
            if args.hippodrome.lower() not in str(r.get("hippodrome", "")).lower():
                continue
        codes.append(r["code_course"])
    return codes


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    settings = load_settings()
    if not settings.has_turfbzh_key:
        console.print("[red]Cle API turf.bzh absente.[/red] Renseigne TURFBZH_API_KEY dans .env.")
        return 1

    try:
        collector = Collector(settings)
    except TurfBzhError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    engine = V5Engine()

    codes = _select_races(collector, args)
    if not codes:
        console.print(f"[yellow]Aucune course de trot attele trouvee pour {args.date}.[/yellow]")
        return 0

    # Enrichissement "smart" : profond pour une course unique, allege en journee.
    single = bool(args.course) or len(codes) == 1
    enrich = not (args.no_enrich or args.fast)
    actor_stats = not (args.no_actor_stats or args.fast)
    depth = args.enrich_depth if args.enrich_depth is not None else (6 if single else 3)

    mode = "course unique (profond)" if single else "journee (allege)"
    extras = []
    if enrich:
        extras.append(f"enrichissement PMU x{depth}")
    if actor_stats:
        extras.append("stats driver/entraineur")
    suffix = f" - {mode}" + (f" - {', '.join(extras)}" if extras else "")
    console.rule(f"Trot attele - {args.date} - {len(codes)} course(s){suffix}")

    all_json = []
    for code in codes:
        try:
            with console.status(f"Collecte {code}..."):
                dossier = collector.build_race(
                    args.date, code,
                    hippodrome=args.hippodrome,
                    with_history=not args.no_history,
                    enrich_history=enrich,
                    enrich_depth=depth,
                    with_actor_stats=actor_stats,
                )
            analysis = engine.analyze(dossier)
        except TurfBzhError as exc:
            console.print(f"[red]{code}: {exc}[/red]")
            continue
        except Exception as exc:  # robustesse : une course ne doit pas tout casser
            console.print(f"[red]{code}: erreur inattendue: {exc}[/red]")
            continue

        render_analysis(console, analysis)
        console.print()
        all_json.append(analysis_to_dict(analysis))

        if args.prompt:
            bundle = build_prompt_bundle(dossier)
            out = settings.out_dir / "prompts" / f"{args.date}_{code}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(bundle, encoding="utf-8")
            console.print(f"[dim]Prompt IA exporte -> {out}[/dim]")

    if args.json and all_json:
        from pathlib import Path
        save_json(all_json, Path(args.json))
        console.print(f"[green]Resultats JSON enregistres -> {args.json}[/green]")

    if collector.tb.last_quota:
        rem = collector.tb.last_quota.get("X-RateLimit-Remaining-Day")
        if rem:
            console.print(f"[dim]Quota API turf.bzh restant aujourd'hui: {rem}[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
