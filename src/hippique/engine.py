"""Moteur d'analyse V5 (deterministe).

Implemente la methode du brief : 4 piliers majeurs (engagement, chronos, forme,
marche), criteres tres importants (distance, parcours, type de depart), criteres
de confirmation (ferrure, opposition, recuperation, retour apres break, etat de
piste, profil), criteres de departage (driver, entraineur, association, niveau),
puis penalisations, recherche de convergences et classement final.

Principes respectes :
- analyse comparative (chaque critere est note par rapport au lot) ;
- aucun critere isole n'elimine un cheval ;
- une donnee manquante => critere "non evaluable" (exclu de la moyenne), jamais 0 ;
- plus les signaux independants convergent, plus la confiance monte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import helpers as H
from .models import (
    CriterionScore,
    HorseDossier,
    HorseEvaluation,
    PastPerf,
    RaceAnalysis,
    RaceDossier,
)

# Poids des criteres (tunable). Les 3 piliers co-majeurs = 1.0 ; marche = 0.6.
WEIGHTS: dict[str, float] = {
    "engagement": 1.0,
    "chronos": 1.0,
    "forme": 1.0,
    "marche": 0.6,
    "distance_ideale": 0.7,
    "aptitude_parcours": 0.7,
    "type_depart": 0.7,
    "ferrure": 0.4,
    "qualite_opposition": 0.4,
    "recuperation": 0.4,
    "retour_break": 0.3,
    "etat_piste": 0.3,
    "profil_course": 0.25,
    "driver": 0.25,
    "entraineur": 0.25,
    "association": 0.25,
    "allocation_valeur": 0.2,
}

LABELS: dict[str, str] = {
    "engagement": "Engagement",
    "chronos": "Chronos / reduction km",
    "forme": "Forme & preparation",
    "marche": "Marche / cote",
    "distance_ideale": "Distance ideale",
    "aptitude_parcours": "Aptitude au parcours",
    "type_depart": "Type de depart",
    "ferrure": "Ferrure",
    "qualite_opposition": "Qualite de l'opposition",
    "recuperation": "Temps de recuperation",
    "retour_break": "Retour apres break",
    "etat_piste": "Etat de la piste",
    "profil_course": "Profil de course",
    "driver": "Driver",
    "entraineur": "Entraineur",
    "association": "Association cheval/driver",
    "allocation_valeur": "Niveau de l'epreuve",
}

# Criteres servant a mesurer les convergences (signaux forts et independants)
CONVERGENCE_KEYS = [
    "engagement", "chronos", "forme", "marche",
    "distance_ideale", "aptitude_parcours", "type_depart",
]
STRONG = 0.66  # seuil "signal positif fort"
WEAK = 0.34    # seuil "signal negatif fort"


def place_to_score(place: int | None, fault: bool) -> float:
    if fault or place is None:
        return 0.15
    table = {1: 1.0, 2: 0.82, 3: 0.68, 4: 0.55, 5: 0.46}
    if place in table:
        return table[place]
    return max(0.1, 0.46 - 0.05 * (place - 5))


@dataclass
class FieldContext:
    """Stats du lot, pour noter chaque cheval de maniere comparative."""

    records_eff: list[float | None]
    rk_similar: list[float | None]  # meilleure RK realisee en conditions proches
    gains: list[float | None]
    cotes: list[float | None]
    elo_jockey: list[float | None]
    elo_entraineur: list[float | None]
    driver_place: list[float | None]
    entr_place: list[float | None]
    median_alloc_hint: float | None


class V5Engine:
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.w = dict(WEIGHTS)
        if weights:
            self.w.update(weights)

    # ------------------------------------------------------------------ #
    def analyze(self, dossier: RaceDossier) -> RaceAnalysis:
        cond = dossier.conditions
        horses = [h for h in dossier.horses if h.partant]
        ctx = self._field_context(horses, cond)

        evals: list[HorseEvaluation] = [self._evaluate(h, horses, ctx, cond) for h in horses]
        evals.sort(key=lambda e: e.global_score, reverse=True)

        recommended = evals[0] if evals else None
        opponents = evals[1:3]
        confidence = self._confidence(evals)
        favori = self._market_favorite(horses)
        explanation = self._explain(cond, recommended, opponents, evals, favori)

        return RaceAnalysis(
            conditions=cond,
            ranking=evals,
            recommended=recommended,
            opponents=opponents,
            confidence_10=confidence,
            explanation=explanation,
        )

    # ------------------------------------------------------------------ #
    def _field_context(self, horses: list[HorseDossier], cond) -> FieldContext:
        records_eff = [self._effective_record(h, cond) for h in horses]
        rk_similar = [self._best_rk_similar(h, cond) for h in horses]
        gains = [h.gains_course for h in horses]
        cotes = [h.cote for h in horses]
        return FieldContext(
            records_eff=records_eff,
            rk_similar=rk_similar,
            gains=gains,
            cotes=cotes,
            elo_jockey=[h.elo_jockey for h in horses],
            elo_entraineur=[h.elo_entraineur for h in horses],
            driver_place=[(h.driver_stats or {}).get("place_rate") for h in horses],
            entr_place=[(h.entraineur_stats or {}).get("place_rate") for h in horses],
            median_alloc_hint=H.median([h.moy_alloc for h in horses if h.moy_alloc]),
        )

    @staticmethod
    def _effective_record(h: HorseDossier, cond) -> float | None:
        """Record ramene a la distance, penalise du rendement de distance.

        Rendre des metres => handicap reel : ~0,7 s/km de reference pour 25 m
        (ordre de grandeur du brief, pas une regle absolue).
        """
        if h.record_distance_s is None:
            return None
        recul = 0
        if h.handicap_distance and cond.distance and h.handicap_distance > cond.distance:
            recul = h.handicap_distance - cond.distance
        penalty = 0.7 * (recul / 25.0)
        return h.record_distance_s + penalty

    # ------------------------------------------------------------------ #
    def _evaluate(self, h, horses, ctx: FieldContext, cond) -> HorseEvaluation:
        crits: list[CriterionScore] = []
        add = lambda k, s, r: crits.append(
            CriterionScore(k, LABELS[k], self.w[k], s, s is not None, r)
        )

        add("engagement", *self._c_engagement(h, horses))
        add("chronos", *self._c_chronos(h, ctx, cond))
        add("forme", *self._c_forme(h))
        add("marche", *self._c_marche(h, ctx))
        add("distance_ideale", *self._c_distance(h, cond))
        add("aptitude_parcours", *self._c_parcours(h, cond))
        add("type_depart", *self._c_depart(h, cond, horses))
        add("ferrure", *self._c_ferrure(h))
        add("qualite_opposition", *self._c_opposition(h, cond))
        add("recuperation", *self._c_recuperation(h))
        add("retour_break", *self._c_retour_break(h))
        add("etat_piste", *self._c_etat_piste(h, cond))
        add("profil_course", *self._c_profil(h))
        add("driver", *self._c_driver(h, ctx))
        add("entraineur", *self._c_entraineur(h, ctx))
        add("association", *self._c_association(h))
        add("allocation_valeur", *self._c_alloc_valeur(h, cond))

        num = sum(c.weight * c.score for c in crits if c.evaluable and c.score is not None)
        den = sum(c.weight for c in crits if c.evaluable and c.score is not None)
        base = num / den if den else 0.5

        signals = sum(
            1 for c in crits
            if c.key in CONVERGENCE_KEYS and c.evaluable and c.score and c.score >= STRONG
        )
        # Bonus de convergence : plusieurs signaux forts independants.
        conv_bonus = 0.03 * max(0, signals - 3)
        global_score = H.clamp(base * (1 + conv_bonus))

        positives = [
            f"{c.label}: {c.rationale}" for c in crits
            if c.evaluable and c.score and c.score >= STRONG and c.rationale
        ]
        negatives = [
            f"{c.label}: {c.rationale}" for c in crits
            if c.evaluable and c.score is not None and c.score <= WEAK and c.rationale
        ]
        penalties = self._penalties(crits)

        return HorseEvaluation(
            num=h.num, nom=h.nom, global_score=global_score,
            confidence_signals=signals, criteria=crits,
            positives=positives, negatives=negatives, penalties=penalties,
        )

    # ================================================================== #
    # PILIERS MAJEURS
    # ================================================================== #
    def _c_engagement(self, h: HorseDossier, horses) -> tuple[float | None, str]:
        parts = []
        score = 0.5
        # Montee / descente de categorie
        if h.rapport_alloc is not None:
            delta = 1.0 - h.rapport_alloc  # <1 => descend (favorable)
            adj = H.clamp(delta, -0.25, 0.25)
            score += adj
            if h.rapport_alloc < 0.9:
                parts.append(f"descend de categorie (rapport alloc {h.rapport_alloc:.2f})")
            elif h.rapport_alloc > 1.1:
                parts.append(f"monte de categorie (rapport alloc {h.rapport_alloc:.2f})")
        elif h.imdc:
            if h.imdc < 0:
                score += 0.12
                parts.append(f"descend d'echelon (IMDC {h.imdc:+.0f})")
            elif h.imdc > 0:
                score -= 0.08
                parts.append(f"monte d'echelon (IMDC {h.imdc:+.0f})")
        # Position dans l'echelle des gains (proche de la limite du recul = classe)
        gr = H.norm_rank(h.gains_course, [x.gains_course for x in horses], lower_better=False)
        if gr is not None:
            score += (gr - 0.5) * 0.25
            if gr >= 0.75:
                parts.append("parmi les plus riches du lot (classe pour la categorie)")
        rationale = "; ".join(parts) if parts else "engagement dans la norme du lot"
        return H.clamp(score), rationale

    def _c_chronos(self, h: HorseDossier, ctx: FieldContext, cond) -> tuple[float | None, str]:
        eff = self._effective_record(h, cond)
        rk_sim = self._best_rk_similar(h, cond)
        rk_top3 = self._best_rk_top3(h)
        rank_rec = H.norm_rank(eff, ctx.records_eff, lower_better=True)
        rank_sim = H.norm_rank(rk_sim, ctx.rk_similar, lower_better=True)
        if rank_rec is None and rank_sim is None:
            return None, "pas de reference chronometrique connue"

        # La RK realisee en conditions proches prime sur le record brut.
        if rank_rec is not None and rank_sim is not None:
            s = 0.45 * rank_rec + 0.55 * rank_sim
        else:
            s = rank_sim if rank_sim is not None else rank_rec

        parts = []
        if h.record_distance_s is not None:
            parts.append(f"record {H.rk_to_str(h.record_distance_s)}")
        if rk_sim is not None:
            parts.append(f"meilleure RK en conditions proches {H.rk_to_str(rk_sim)}")
        if rk_top3 is not None:
            parts.append(f"RK dans les 3 premiers {H.rk_to_str(rk_top3)}")
        if h.handicap_distance and cond.distance and h.handicap_distance > cond.distance:
            parts.append(f"rend {h.handicap_distance - cond.distance} m (rendement integre)")
        if s >= 0.8:
            parts.append("chrono parmi les meilleurs du lot")
        elif s <= 0.3:
            parts.append("chrono insuffisant par rapport au lot")
        return H.clamp(s), "; ".join(parts) if parts else "chrono evalue"

    def _c_forme(self, h: HorseDossier) -> tuple[float | None, str]:
        perfs = self._attele_perfs(h, limit=4)
        if not perfs and h.taux_place is None:
            return None, "pas d'element de forme recent"
        parts = []
        score = None
        if perfs:
            weights = [1.0, 0.75, 0.55, 0.4][: len(perfs)]
            ps = [place_to_score(p.place, p.fault) for p in perfs]
            score = sum(w * x for w, x in zip(weights, ps)) / sum(weights)
            # Echantillon faible -> regression vers la moyenne (prudence)
            shrink = min(1.0, len(perfs) / 3.0)
            score = 0.5 + (score - 0.5) * shrink
            seq = "".join(p.raw_rank or "?" for p in perfs)
            parts.append(f"{len(perfs)} dernieres (attele): {seq}")
            if len(perfs) < 3:
                parts.append("peu de courses (note prudente)")
            # tendance ascendante
            if len(perfs) >= 3 and perfs[0].place and perfs[-1].place:
                if perfs[0].place < perfs[-1].place:
                    score = H.clamp(score + 0.06)
                    parts.append("forme ascendante")
            # derniere ligne droite (commentaire enrichi - maniere de finir)
            if perfs[0].commentaire:
                c = perfs[0].commentaire.lower()
                if any(k in c for k in ("termine fort", "bien fini", "accel", "revient", "finit", "sans etre sollicit", "sans etre sollicité", "facile")):
                    score = H.clamp(score + 0.05)
                    parts.append("belle derniere ligne droite au dernier parcours")
                elif any(k in c for k in ("faibli", "lache", "lâche", "recule", "fini sur le reculoir", "distanc")):
                    score = H.clamp(score - 0.05)
                    parts.append("a faibli dans le dernier parcours")
            # amelioration du chrono (RK en progres)
            rks = [p.reduction_km_s for p in perfs if p.reduction_km_s and not p.fault]
            if len(rks) >= 3:
                older = H.median(rks[1:])
                if older and rks[0] < older - 0.4:
                    score = H.clamp(score + 0.05)
                    parts.append(f"chrono en progres ({H.rk_to_str(rks[0])} vs {H.rk_to_str(older)})")
        # croisement avec le taux de place carriere
        if h.taux_place is not None:
            tp = h.taux_place
            score = tp if score is None else H.clamp(0.7 * score + 0.3 * tp)
            parts.append(f"taux de place {tp*100:.0f}%")
        return score, "; ".join(parts)

    def _c_marche(self, h: HorseDossier, ctx: FieldContext) -> tuple[float | None, str]:
        if h.cote is None:
            return None, "cote indisponible"
        prob = H.norm_rank(h.cote, ctx.cotes, lower_better=True) or 0.5
        parts = [f"cote {h.cote:g}"]

        # Mouvement : baisse = cheval joue (favorable) ; hausse = delaisse.
        move = 0.5
        drift = h.cote_drift_matin_pct   # negatif = raccourcit depuis le matin
        recent = h.cote_move_recent_pct  # negatif = baisse sur ~5 min (tardive)
        if drift is not None:
            move = H.clamp(0.5 - drift / 50.0)
            if drift <= -12:
                parts.append(f"nettement joue depuis le matin ({drift:+.0f}%)")
            elif drift >= 15:
                parts.append(f"delaisse depuis le matin ({drift:+.0f}%)")
        if recent is not None and recent <= -10:
            move = H.clamp(move + 0.08)
            parts.append(f"baisse tardive ({recent:+.0f}% sur ~5 min)")
        elif recent is not None and recent >= 12:
            move = H.clamp(move - 0.05)
            parts.append("remonte en toute fin de pari")
        elif h.cote_dernier_mouvement == "baisse":
            move = H.clamp(move + 0.03)

        score = H.clamp(0.6 * prob + 0.4 * move)
        if h.favori:
            parts.append("favori")
        # Ecart a la Cote_BZH (value) : signale, sans gonfler la confiance marche.
        if h.ecart_value_pct is not None and h.ecart_value_pct >= 25:
            parts.append(f"cote plus genereuse que le modele (+{h.ecart_value_pct:.0f}%, value)")
        elif h.ecart_value_pct is not None and h.ecart_value_pct <= -25:
            parts.append("surcote par rapport au modele")
        return score, "; ".join(parts)

    # ================================================================== #
    # TRES IMPORTANTS
    # ================================================================== #
    def _c_distance(self, h: HorseDossier, cond) -> tuple[float | None, str]:
        perfs = self._attele_perfs(h, limit=20)
        if len(perfs) < 2 or not cond.distance:
            return None, "historique distance insuffisant"
        near = [p for p in perfs if p.distance and abs(p.distance - cond.distance) <= 200]
        far = [p for p in perfs if p.distance and abs(p.distance - cond.distance) > 400]
        if len(near) < 2:
            return None, "peu de courses sur une distance comparable"
        near_s = sum(place_to_score(p.place, p.fault) for p in near) / len(near)
        parts = [f"{len(near)} courses a ~{cond.distance}m -> perf moy {near_s:.2f}"]
        if far:
            far_s = sum(place_to_score(p.place, p.fault) for p in far) / len(far)
            if near_s - far_s > 0.1:
                parts.append("nettement meilleur sur cette distance")
        return H.clamp(near_s), "; ".join(parts)

    def _c_parcours(self, h: HorseDossier, cond) -> tuple[float | None, str]:
        """Aptitude au parcours conjoint : hippodrome, puis affinage par bande de
        distance, corde et type de depart. Plus la couche est specifique, plus
        elle pese."""
        perfs = self._attele_perfs(h, limit=25)
        if not cond.hippodrome:
            return None, "hippodrome inconnu"
        same = [p for p in perfs if p.hippodrome and _norm(p.hippodrome) == _norm(cond.hippodrome)]
        if len(same) < 2:
            return None, "peu de references sur cet hippodrome"

        def mean(ps: list[PastPerf]) -> float:
            return sum(place_to_score(p.place, p.fault) for p in ps) / len(ps)

        base = mean(same)
        layers: list[tuple[float, float]] = [(base, 1.0)]
        parts = [f"{len(same)} courses a {cond.hippodrome} -> perf moy {base:.2f}"]

        if cond.distance:
            sd = [p for p in same if p.distance and abs(p.distance - cond.distance) <= 200]
            if len(sd) >= 2:
                layers.append((mean(sd), 1.3))
                parts.append(f"dont {len(sd)} a ~{cond.distance} m")
        if cond.corde:
            sc = [p for p in same if p.corde and p.corde == cond.corde]
            if len(sc) >= 2:
                layers.append((mean(sc), 1.0))
                parts.append(f"corde {cond.corde.split('_')[-1].lower()} confirmee")
        if cond.depart_type:
            sp = [p for p in same if p.depart == cond.depart_type]
            if len(sp) >= 2:
                layers.append((mean(sp), 1.0))
                parts.append(f"en {cond.depart_type.lower()} sur l'hippodrome")

        s = sum(v * w for v, w in layers) / sum(w for _, w in layers)
        return H.clamp(s), "; ".join(parts)

    def _c_depart(self, h: HorseDossier, cond, horses) -> tuple[float | None, str]:
        if not cond.depart_type:
            return None, "type de depart inconnu"
        perfs = self._attele_perfs(h, limit=12)
        same = [p for p in perfs if p.depart == cond.depart_type]
        # Sans enrichissement : on s'appuie sur le risque de faute et les D recents
        faults_recent = sum(1 for p in perfs[:6] if p.fault)
        base = 0.6
        parts = [cond.depart_type.lower()]
        if len(same) >= 2:
            s = sum(place_to_score(p.place, p.fault) for p in same) / len(same)
            parts.append(f"{len(same)} courses en {cond.depart_type.lower()} -> perf {s:.2f}")
            base = s
        if h.taux_incident is not None:
            base -= H.clamp(h.taux_incident, 0, 0.6) * 0.3
            if h.taux_incident >= 0.25:
                parts.append(f"taux de faute eleve ({h.taux_incident:.2f})")
        if cond.depart_type == "VOLTE" and faults_recent >= 2:
            base -= 0.1
            parts.append(f"{faults_recent} fautes sur 6 courses (risque au volte)")
        if h.d4_risque_faute == "eleve":
            base -= 0.08
            parts.append("D4 a risque de faute eleve")
        note = " (type infere)" if cond.depart_infere else ""
        return H.clamp(base), "; ".join(parts) + note

    # ================================================================== #
    # CONFIRMATION
    # ================================================================== #
    def _c_ferrure(self, h: HorseDossier) -> tuple[float | None, str]:
        perfs = self._attele_perfs(h, limit=20)
        by_cfg: dict[str, list[float]] = {}
        for p in perfs:
            if p.ferrure:
                by_cfg.setdefault(p.ferrure, []).append(place_to_score(p.place, p.fault))
        cur = h.ferrure
        parts = []
        if cur and by_cfg.get(cur) and len(by_cfg[cur]) >= 2:
            cur_s = sum(by_cfg[cur]) / len(by_cfg[cur])
            others = [v for k, vs in by_cfg.items() if k != cur for v in vs]
            base = 0.5 + (cur_s - 0.5) * 0.6
            if others:
                oth_s = sum(others) / len(others)
                if cur_s - oth_s > 0.1:
                    base = H.clamp(base + 0.12)
                    parts.append(f"meilleure config historique ({H.FERRURE_LABELS.get(cur, cur)})")
                elif oth_s - cur_s > 0.1:
                    base = H.clamp(base - 0.1)
                    parts.append(f"config {H.FERRURE_LABELS.get(cur, cur)} moins productive")
            if not parts:
                parts.append(f"{H.FERRURE_LABELS.get(cur, cur)} (neutre)")
            # pas de bonus automatique : on reste prudent
            return H.clamp(base), "; ".join(parts)
        # Pas d'historique de ferrure exploitable -> aucune bonification automatique
        if h.changement_ferrure == "Oui":
            return None, "changement de ferrure sans preuve historique"
        return None, "ferrure sans historique probant"

    def _c_opposition(self, h: HorseDossier, cond) -> tuple[float | None, str]:
        perfs = self._attele_perfs(h, limit=4)
        if not perfs or not cond.allocation:
            return None, "niveau des sorties inconnu"
        num = den = 0.0
        for p, w in zip(perfs, [1.0, 0.7, 0.5, 0.35]):
            if not p.allocation:
                continue
            lvl = H.clamp(p.allocation / cond.allocation, 0.5, 2.0)
            num += w * place_to_score(p.place, p.fault) * lvl
            den += w * lvl
        if den == 0:
            return None, "allocations des sorties inconnues"
        s = H.clamp(num / den)
        # Echantillon faible -> prudence
        shrink = min(1.0, len(perfs) / 3.0)
        s = H.clamp(0.5 + (s - 0.5) * shrink)
        parts = []
        best = max(perfs, key=lambda p: (p.allocation or 0))
        if best.allocation and best.allocation > cond.allocation and (best.place or 9) <= 4:
            parts.append(f"bonne tenue dans un lot plus releve ({best.allocation}EUR)")
        parts.append(f"forme ponderee par le niveau: {s:.2f}")
        return s, "; ".join(parts)

    def _c_recuperation(self, h: HorseDossier) -> tuple[float | None, str]:
        if h.repos is None:
            return None, "ecart depuis la derniere course inconnu"
        gaps = self._history_gaps(h)
        hab = H.median(gaps) if gaps else None
        r = h.repos
        if hab:
            ratio = r / hab if hab else 1
            if 0.6 <= ratio <= 1.6:
                return 0.68, f"{r} j (proche de son rythme habituel ~{hab:.0f} j)"
            if ratio < 0.5:
                return 0.42, f"{r} j (delai court vs habituel ~{hab:.0f} j)"
            if ratio > 2.2:
                return 0.45, f"{r} j (delai long vs habituel ~{hab:.0f} j)"
            return 0.58, f"{r} j (ecart habituel ~{hab:.0f} j)"
        # heuristique absolue
        if 10 <= r <= 30:
            return 0.62, f"{r} j (delai classique)"
        if r < 7:
            return 0.45, f"{r} j (delai tres court)"
        return 0.5, f"{r} j"

    def _c_retour_break(self, h: HorseDossier) -> tuple[float | None, str]:
        if h.repos is None or h.repos <= 60:
            return None, ""  # pas une rentree -> critere non applicable
        perfs = self._attele_perfs(h, limit=20)
        # chercher les rentrees passees (gap > 60) et la perf a la reprise
        good_returns = 0
        total_returns = 0
        dates = [p for p in perfs if p.date]
        for i in range(len(dates) - 1):
            g = H.days_between(dates[i].date, dates[i + 1].date)
            if g and g > 60:
                total_returns += 1
                if dates[i].place and dates[i].place <= 5 and not dates[i].fault:
                    good_returns += 1
        if total_returns:
            rate = good_returns / total_returns
            s = H.clamp(0.4 + 0.4 * rate)
            txt = "court bien a la rentree" if rate >= 0.5 else "a besoin de courses apres un break"
            return s, f"rentree apres {h.repos} j ({txt}, {good_returns}/{total_returns})"
        return 0.45, f"rentree apres {h.repos} j (pas de precedent exploitable)"

    def _c_etat_piste(self, h: HorseDossier, cond) -> tuple[float | None, str]:
        if not cond.penetrometre:
            return None, "etat de piste non renseigne (frequent au trot)"
        return 0.5, "etat de piste pris en compte"

    def _c_profil(self, h: HorseDossier) -> tuple[float | None, str]:
        # Prudence : profil deduit uniquement si commentaires enrichis disponibles
        perfs = self._attele_perfs(h, limit=5)
        comments = [p.commentaire.lower() for p in perfs if p.commentaire]
        if not comments:
            return None, "profil de course indetermine (prudence)"
        leader = sum(1 for c in comments if "en tete" in c or "mene" in c or "anime" in c)
        finisseur = sum(1 for c in comments if "termine fort" in c or "revient" in c or "accel" in c)
        if finisseur > leader:
            return 0.55, "profil finisseur (indicatif)"
        if leader > finisseur:
            return 0.52, "profil qui aime aller devant (indicatif)"
        return 0.5, "profil mixte"

    # ================================================================== #
    # DEPARTAGE
    # ================================================================== #
    def _c_driver(self, h: HorseDossier, ctx: FieldContext) -> tuple[float | None, str]:
        comps: list[tuple[float, float]] = []
        parts: list[str] = []
        s_elo = H.norm_rank(h.elo_jockey, ctx.elo_jockey, lower_better=False)
        if s_elo is not None:
            comps.append((s_elo, 1.0))
            parts.append(f"ELO {h.elo_jockey:.0f}")
            if s_elo >= 0.8:
                parts.append("un des meilleurs drivers du lot")
        st = h.driver_stats or {}
        # Forme 90 j (place_rate) notee de maniere comparative au lot
        s_form = H.norm_rank(st.get("place_rate"), ctx.driver_place, lower_better=False)
        if s_form is not None and st.get("n"):
            w = min(1.0, (st["n"] or 0) / 15.0)
            comps.append((s_form, 0.8 * w))
            parts.append(f"forme 90j {st['place_rate']*100:.0f}% place ({st['n']} crs)")
        # Reussite specifique sur l'hippodrome
        hr, hw = st.get("hippo_rides"), st.get("hippo_wins")
        if hr and hr >= 3:
            rate = (hw or 0) / hr
            comps.append((H.clamp(0.4 + rate * 1.6), 0.6))
            parts.append(f"{hw or 0}/{hr} sur l'hippodrome")
        if not comps:
            return None, "niveau du driver inconnu"
        s = sum(v * w for v, w in comps) / sum(w for _, w in comps)
        return H.clamp(s), "; ".join(parts)

    def _c_entraineur(self, h: HorseDossier, ctx: FieldContext) -> tuple[float | None, str]:
        comps: list[tuple[float, float]] = []
        parts: list[str] = []
        s_elo = H.norm_rank(h.elo_entraineur, ctx.elo_entraineur, lower_better=False)
        if s_elo is not None:
            comps.append((s_elo, 1.0))
            parts.append(f"ELO {h.elo_entraineur:.0f}")
        st = h.entraineur_stats or {}
        s_form = H.norm_rank(st.get("place_rate"), ctx.entr_place, lower_better=False)
        if s_form is not None and st.get("n"):
            w = min(1.0, (st["n"] or 0) / 12.0)
            comps.append((s_form, 0.8 * w))
            parts.append(f"ecurie {st['place_rate']*100:.0f}% place (90j)")
        hr, hw = st.get("hippo_rides"), st.get("hippo_wins")
        if hr and hr >= 3:
            rate = (hw or 0) / hr
            comps.append((H.clamp(0.4 + rate * 1.6), 0.5))
            parts.append(f"{hw or 0}/{hr} sur l'hippodrome")
        if not comps:
            return None, "niveau de l'entraineur inconnu"
        s = sum(v * w for v, w in comps) / sum(w for _, w in comps)
        return H.clamp(s), "; ".join(parts)

    def _c_association(self, h: HorseDossier) -> tuple[float | None, str]:
        if h.synergie_jch is None:
            return None, "synergie cheval/driver inconnue"
        s = H.clamp(h.synergie_jch / 100.0)
        return s, f"synergie cheval/driver {h.synergie_jch:.0f}%"

    def _c_alloc_valeur(self, h: HorseDossier, cond) -> tuple[float | None, str]:
        if not (h.moy_alloc and cond.allocation):
            return None, ""
        rel = (h.moy_alloc - cond.allocation) / (h.moy_alloc + 1)
        s = H.clamp(0.5 + rel * 0.4)
        if rel > 0.15:
            return s, f"descend en valeur (habitue a {h.moy_alloc}EUR, jour {cond.allocation}EUR)"
        if rel < -0.15:
            return s, f"monte en valeur (habitue a {h.moy_alloc}EUR)"
        return s, "niveau d'epreuve habituel"

    # ================================================================== #
    # PENALISATIONS (narratif ; deja refletees dans les scores)
    # ================================================================== #
    @staticmethod
    def _penalties(crits: list[CriterionScore]) -> list[str]:
        out = []
        by = {c.key: c for c in crits}
        def low(k):
            c = by.get(k)
            return c and c.evaluable and c.score is not None and c.score <= WEAK
        if low("chronos"):
            out.append("chronos insuffisants (forte penalisation, pas eliminatoire)")
        if low("engagement"):
            out.append("engagement defavorable")
        if low("forme"):
            out.append("forme recente moyenne")
        if low("distance_ideale"):
            out.append("distance peu favorable")
        return out

    # ================================================================== #
    # CONFIANCE & EXPLICATION
    # ================================================================== #
    def _confidence(self, evals: list[HorseEvaluation]) -> float:
        if not evals:
            return 0.0
        s1 = evals[0].global_score
        s2 = evals[1].global_score if len(evals) > 1 else s1 - 0.1
        gap = max(0.0, s1 - s2)
        sig = evals[0].confidence_signals
        pen = len(evals[0].penalties)
        conf = 3.0 + 5.0 * s1 + 4.0 * gap + 0.4 * max(0, sig - 2) - 0.4 * pen
        return round(H.clamp(conf, 1.0, 10.0), 1)

    @staticmethod
    def _market_favorite(horses):
        coted = [h for h in horses if h.cote is not None]
        return min(coted, key=lambda h: h.cote) if coted else None

    def _explain(self, cond, rec: HorseEvaluation | None, opp, evals, favori) -> str:
        if not rec:
            return "Aucun partant exploitable."
        ctx_bits = []
        if cond.distance:
            ctx_bits.append(f"{cond.distance} m")
        if cond.corde:
            ctx_bits.append("corde " + cond.corde.split("_")[-1].lower())
        if cond.depart_type:
            ctx_bits.append(cond.depart_type.lower() + (" (infere)" if cond.depart_infere else ""))
        if cond.classe_groupe:
            ctx_bits.append(cond.classe_groupe)
        contexte = ", ".join(ctx_bits)

        # top criteres contributifs du recommande
        contrib = sorted(
            [c for c in rec.criteria if c.evaluable and c.score is not None],
            key=lambda c: c.weight * (c.score - 0.5),
            reverse=True,
        )
        forces = [f"{c.label.lower()} ({c.rationale})" for c in contrib[:4] if c.score >= 0.55 and c.rationale]

        lines = []
        lines.append(
            f"{rec.nom} (#{rec.num}) est le meilleur compromis dans les conditions du jour "
            f"({contexte})."
        )
        if forces:
            lines.append("Ce qui fait la difference : " + " ; ".join(forces) + ".")
        sig_word = "signal" if rec.confidence_signals <= 1 else "signaux"
        lines.append(
            f"Convergence de {rec.confidence_signals} {sig_word} independant"
            f"{'s' if rec.confidence_signals > 1 else ''} parmi les piliers et criteres "
            f"majeurs (score global {rec.global_score:.2f})."
        )
        # Divergence avec le marche : le marche confirme ou contredit, sans remplacer
        if favori is not None:
            if favori.num == rec.num:
                lines.append("Le marche confirme (c'est aussi le favori).")
            else:
                lines.append(
                    f"Le marche prefere {favori.nom} (#{favori.num}, cote {favori.cote:g}) : "
                    f"l'analyse fondamentale pointe ailleurs, le marche reste un signal a surveiller."
                )
        if rec.penalties:
            lines.append("Reserves : " + " ; ".join(rec.penalties) + ".")
        if opp:
            opp_txt = ", ".join(f"{o.nom} (#{o.num}, {o.global_score:.2f})" for o in opp)
            lines.append(f"Principaux opposants : {opp_txt}.")
        return " ".join(lines)

    # ================================================================== #
    # OUTILS
    # ================================================================== #
    @staticmethod
    def _attele_perfs(h: HorseDossier, limit: int) -> list[PastPerf]:
        # On ne melange pas le monte : seules les sorties a l'attele comptent pour
        # la forme, les chronos, la distance, le parcours et le depart. Le monte
        # reste une information complementaire (conserve dans le dossier/prompt).
        att = [p for p in h.historique if H.is_attele(p.discipline)]
        return att[:limit]

    def _similar_perfs(self, h: HorseDossier, cond) -> list[PastPerf]:
        """Sorties a l'attele dans des conditions proches (distance +/-150 m,
        meme type de depart si connu)."""
        perfs = self._attele_perfs(h, limit=25)
        if not cond.distance:
            return perfs
        near = [p for p in perfs if p.distance and abs(p.distance - cond.distance) <= 150]
        if cond.depart_type:
            typed = [p for p in near if p.depart == cond.depart_type]
            if typed:
                return typed
        return near

    def _best_rk_similar(self, h: HorseDossier, cond) -> float | None:
        rks = [
            p.reduction_km_s
            for p in self._similar_perfs(h, cond)
            if p.reduction_km_s and not p.fault
        ]
        return min(rks) if rks else None

    def _best_rk_top3(self, h: HorseDossier) -> float | None:
        rks = [
            p.reduction_km_s
            for p in self._attele_perfs(h, limit=25)
            if p.reduction_km_s and p.place and p.place <= 3 and not p.fault
        ]
        return min(rks) if rks else None

    @staticmethod
    def _history_gaps(h: HorseDossier) -> list[float]:
        dates = [p.date for p in h.historique if p.date]
        gaps = []
        for i in range(len(dates) - 1):
            g = H.days_between(dates[i], dates[i + 1])
            if g is not None:
                gaps.append(float(g))
        return gaps


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())
