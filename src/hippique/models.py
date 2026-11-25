"""Modeles de donnees : dossier unifie d'une course et de ses chevaux.

Ces structures fusionnent turf.bzh (source principale) et PMU (complement),
et servent d'entree au moteur d'analyse V5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PastPerf:
    """Une performance passee d'un cheval (reconstituee depuis l'historique)."""

    date: str | None = None
    hippodrome: str | None = None
    code_course: str | None = None
    discipline: str | None = None
    distance: int | None = None
    allocation: int | None = None
    nombre_partants: int | None = None
    place: int | None = None  # rang numerique
    fault: bool = False  # disqualifie / arret / chute
    raw_rank: str | None = None
    earnings: float | None = None
    ferrure: str | None = None  # normalisee
    # Enrichissement PMU (optionnel)
    reduction_km_s: float | None = None
    corde: str | None = None
    depart: str | None = None  # AUTOSTART / VOLTE
    commentaire: str | None = None
    incident: str | None = None


@dataclass
class RaceConditions:
    """Les conditions de la course du jour (section 1 du brief)."""

    date: str
    code_course: str
    hippodrome: str | None = None
    pays: str | None = None
    discipline: str | None = None
    distance: int | None = None
    allocation: int | None = None
    classe_groupe: str | None = None
    corde: str | None = None  # CORDE_GAUCHE / CORDE_DROITE
    parcours: str | None = None  # GRANDE PISTE / PETITE PISTE / sable...
    depart_type: str | None = None  # AUTOSTART / VOLTE
    depart_infere: bool = False
    penetrometre: Any = None
    heure: str | None = None
    nombre_partants: int | None = None
    is_quinte: bool = False
    conditions_txt: str | None = None


@dataclass
class HorseDossier:
    """Tout ce qu'on sait d'un cheval pour la course du jour."""

    num: int | None = None
    nom: str | None = None
    idcheval: int | None = None
    age: int | None = None
    sexe: str | None = None
    partant: bool = True

    # Acteurs
    driver: str | None = None
    entraineur: str | None = None
    proprietaire: str | None = None
    idjockey: int | None = None
    identraineur: int | None = None

    # Engagement / gains / niveau
    gains_totaux: float | None = None
    gains_course: float | None = None
    courses_courues: int | None = None
    nombre_victoire: int | None = None
    nombre_place: int | None = None
    imdc: float | None = None
    classe_groupe: str | None = None
    allocation_jour: int | None = None
    alloc_precedente: int | None = None
    rapport_alloc: float | None = None
    moy_alloc: float | None = None
    handicap_distance: int | None = None  # recul eventuel (PMU)

    # Chronos
    record_distance_s: float | None = None

    # Forme
    taux_place: float | None = None
    taux_victoire: float | None = None
    taux_incident: float | None = None
    turf_points: int | None = None
    tpch_90: int | None = None
    moy_tpch_365: int | None = None
    repos: int | None = None
    ex_fav: str | None = None
    popularite: float | None = None
    evo_popularite: float | None = None
    musique: str | None = None
    musique_parsed: list[dict[str, Any]] = field(default_factory=list)

    # Distance / parcours
    evo_distance: str | None = None

    # Ferrure
    ferrure: str | None = None  # normalisee
    ferrure_prec: str | None = None
    changement_ferrure: str | None = None
    # D4
    d4_statut: str | None = None
    d4_risque_faute: str | None = None

    # Acteurs : indicateurs
    elo_cheval: float | None = None
    sigma_horse: float | None = None
    elo_jockey: float | None = None
    elo_entraineur: float | None = None
    rang_j: int | None = None
    tpj_90: int | None = None
    synergie_jch: float | None = None

    # IA turf.bzh (reference, pas une verite)
    note_ia: float | None = None
    ia_gagnant: float | None = None
    cote_bzh: float | None = None

    # Marche
    cote: float | None = None
    cote_matin: float | None = None
    cote_tendance_pct: float | None = None  # mouvement court terme (brut PMU)
    cote_drift_matin_pct: float | None = None  # evolution depuis le matin (progressive)
    cote_move_recent_pct: float | None = None  # evolution sur ~5 min (tardive)
    cote_dernier_mouvement: str | None = None  # baisse / hausse / stable
    ecart_value_pct: float | None = None  # ecart cote vs Cote_BZH (value)
    favori: bool = False

    # Stats acteurs (turf.bzh /personnes)
    driver_stats: dict[str, Any] = field(default_factory=dict)
    entraineur_stats: dict[str, Any] = field(default_factory=dict)

    # PMU complement
    oeilleres: str | None = None
    avis_entraineur: str | None = None
    driver_change: bool | None = None
    commentaire_derniere: str | None = None

    # Historique reconstitue
    historique: list[PastPerf] = field(default_factory=list)

    # Donnees brutes (pour debug / export)
    raw_turfbzh: dict[str, Any] = field(default_factory=dict)
    raw_pmu: dict[str, Any] = field(default_factory=dict)


@dataclass
class RaceDossier:
    conditions: RaceConditions
    horses: list[HorseDossier] = field(default_factory=list)
    renifleur: dict[str, Any] | None = None
    sources: dict[str, bool] = field(default_factory=dict)  # quelles sources ont repondu


# --------------------------------------------------------------------------- #
# Resultats du moteur d'analyse
# --------------------------------------------------------------------------- #
@dataclass
class CriterionScore:
    key: str
    label: str
    weight: float
    score: float | None  # [0,1] ou None si non evaluable
    evaluable: bool
    rationale: str = ""


@dataclass
class HorseEvaluation:
    num: int | None
    nom: str | None
    global_score: float  # [0,1]
    confidence_signals: int  # nb de convergences positives
    criteria: list[CriterionScore] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)


@dataclass
class RaceAnalysis:
    conditions: RaceConditions
    ranking: list[HorseEvaluation]
    recommended: HorseEvaluation | None
    opponents: list[HorseEvaluation]
    confidence_10: float
    explanation: str
