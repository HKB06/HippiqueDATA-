"""Rendu des resultats : console (rich), JSON, et export du prompt pour IA."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import helpers as H
from .models import PastPerf, RaceAnalysis, RaceDossier
from .prompt_v5 import INSTRUCTIONS_DONNEES, PROMPT_V5


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def _perf_dict(p: PastPerf) -> dict[str, Any]:
    d = asdict(p)
    d["reduction_km"] = H.rk_to_str(p.reduction_km_s)
    return d


def dossier_to_dict(dossier: RaceDossier, *, with_history: bool = True) -> dict[str, Any]:
    cond = asdict(dossier.conditions)
    horses = []
    for h in dossier.horses:
        hd = asdict(h)
        hd.pop("raw_turfbzh", None)
        hd.pop("raw_pmu", None)
        hd.pop("musique_parsed", None)
        hd["record_km"] = H.rk_to_str(h.record_distance_s)
        if with_history:
            hd["historique"] = [_perf_dict(p) for p in h.historique]
        else:
            hd.pop("historique", None)
        horses.append(hd)
    return {"conditions": cond, "sources": dossier.sources, "partants": horses}


def analysis_to_dict(analysis: RaceAnalysis) -> dict[str, Any]:
    def ev(e):
        return {
            "num": e.num,
            "nom": e.nom,
            "score": round(e.global_score, 3),
            "signaux_convergents": e.confidence_signals,
            "positifs": e.positives,
            "negatifs": e.negatives,
            "penalisations": e.penalties,
            "criteres": [
                {
                    "critere": c.label,
                    "poids": c.weight,
                    "score": None if c.score is None else round(c.score, 3),
                    "evaluable": c.evaluable,
                    "commentaire": c.rationale,
                }
                for c in e.criteria
            ],
        }

    return {
        "conditions": asdict(analysis.conditions),
        "recommande": ev(analysis.recommended) if analysis.recommended else None,
        "opposants": [ev(o) for o in analysis.opponents],
        "indice_confiance_sur_10": analysis.confidence_10,
        "explication": analysis.explanation,
        "classement": [ev(e) for e in analysis.ranking],
    }


def save_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Export du prompt pour IA
# --------------------------------------------------------------------------- #
def build_prompt_bundle(dossier: RaceDossier) -> str:
    """PROMPT V5 + instructions + dossier structure (JSON) d'une course."""
    data = dossier_to_dict(dossier, with_history=True)
    return (
        PROMPT_V5
        + "\n"
        + INSTRUCTIONS_DONNEES
        + "\n# DONNEES DE LA COURSE (JSON)\n```json\n"
        + json.dumps(data, ensure_ascii=False, indent=2)
        + "\n```\n"
    )


# --------------------------------------------------------------------------- #
# Rendu console
# --------------------------------------------------------------------------- #
def render_analysis(console: Console, analysis: RaceAnalysis) -> None:
    c = analysis.conditions
    rec = analysis.recommended

    header = (
        f"[bold]{c.code_course}[/bold] {c.hippodrome or '?'} - {c.distance or '?'} m "
        f"- {c.heure or '?'} - {c.classe_groupe or ''} - alloc {c.allocation or '?'} EUR\n"
        f"corde {_corde(c.corde)} | depart {c.depart_type or '?'}"
        f"{' (infere)' if c.depart_infere else ''} | parcours {c.parcours or '-'}"
    )
    console.print(Panel(header, title="Course", expand=False))

    if not rec:
        console.print("[red]Aucun partant exploitable.[/red]")
        return

    # Bloc resultat (format section 24)
    conf = analysis.confidence_10
    color = "green" if conf >= 7 else "yellow" if conf >= 5 else "red"
    res = (
        f"[bold green]1 - Recommande :[/bold green] #{rec.num} {rec.nom}\n"
        f"[bold]2 - Opposants :[/bold] "
        + ", ".join(f"#{o.num} {o.nom}" for o in analysis.opponents)
        + f"\n[bold]3 - Confiance :[/bold] [{color}]{conf}/10[/{color}]\n"
        f"[bold]4 - Explication :[/bold] {analysis.explanation}"
    )
    console.print(Panel(res, title="Verdict V5", border_style=color))

    # Tableau de classement
    table = Table(title="Classement et criteres cles", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("Cheval")
    table.add_column("Score", justify="right")
    table.add_column("Conv.", justify="right")
    table.add_column("Points forts / reserves", overflow="fold")
    for e in analysis.ranking:
        pts = "; ".join(e.positives[:2])
        if e.penalties:
            pts += "  |  RESERVE: " + "; ".join(e.penalties[:1])
        table.add_row(
            str(e.num),
            str(e.nom or "?")[:22],
            f"{e.global_score:.2f}",
            str(e.confidence_signals),
            pts[:90],
        )
    console.print(table)


def _corde(corde: str | None) -> str:
    if not corde:
        return "?"
    return corde.split("_")[-1].lower()
