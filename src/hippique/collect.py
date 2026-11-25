"""Collecteur : construit un RaceDossier complet en fusionnant turf.bzh et PMU.

turf.bzh est la source principale (indicateurs, ELO, IA, forme, historique).
PMU complete : conditions (corde, piste, type de depart), ferrure/gains/cote
tendance du jour, et - en option - la reduction kilometrique et le commentaire
de course des sorties passees (manière de finir).
"""

from __future__ import annotations

from typing import Any

from . import helpers as H
from .clients.pmu import PmuClient, PmuError
from .clients.turfbzh import DISCIPLINE_ATTELE, TurfBzhClient, TurfBzhError
from .config import Settings
from .models import HorseDossier, PastPerf, RaceConditions, RaceDossier


class Collector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tb = TurfBzhClient(settings)
        self.pmu: PmuClient | None = PmuClient(settings) if settings.pmu_enabled else None

    # ------------------------------------------------------------------ #
    # Liste des courses attelees francaises du jour
    # ------------------------------------------------------------------ #
    def attele_races(self, date: str, france_only: bool = True) -> list[dict[str, Any]]:
        """Retourne les courses de trot attele du jour.

        Chaque entree : {code_course, hippodrome, heure, distance, allocation,
        nombre_partants, is_quinte, pays}.
        """
        prog = self.tb.programme(date=date)
        races = prog.get("races", []) if isinstance(prog, dict) else []
        attele = [r for r in races if H.is_attele(r.get("discipline"))]

        pays_by_code = self._pmu_pays_by_code(date)
        out = []
        for r in attele:
            code = r.get("code_course")
            pays = pays_by_code.get(code)
            if france_only and pays_by_code and pays and pays != "FRA":
                continue
            # Si PMU indisponible, on ne peut pas filtrer : on garde tout en le signalant.
            r2 = dict(r)
            r2["pays"] = pays
            out.append(r2)
        return out

    def _pmu_pays_by_code(self, date: str) -> dict[str, str | None]:
        if not self.pmu:
            return {}
        try:
            return {c.code: c.pays for c in self.pmu.programme(date)}
        except PmuError:
            return {}

    # ------------------------------------------------------------------ #
    # Construction d'un dossier de course
    # ------------------------------------------------------------------ #
    def build_race(
        self,
        date: str,
        code: str,
        *,
        hippodrome: str | None = None,
        with_history: bool = True,
        enrich_history: bool = False,
        enrich_depth: int = 4,
        with_actor_stats: bool = True,
    ) -> RaceDossier:
        sources: dict[str, bool] = {"turfbzh": False, "pmu": False}

        course = self.tb.course(date, code, hippodrome=hippodrome)
        sources["turfbzh"] = True
        partants = course.get("partants", []) if isinstance(course, dict) else []

        # --- Conditions (turf.bzh + PMU) ---
        cond = RaceConditions(
            date=date,
            code_course=code,
            hippodrome=course.get("hippodrome"),
            discipline=course.get("discipline"),
            distance=H.safe_int(course.get("distance")),
            allocation=H.safe_int(course.get("allocation")),
            classe_groupe=course.get("classe_groupe"),
            heure=course.get("heure"),
            nombre_partants=H.safe_int(course.get("nombre_partants_reel"))
            or H.safe_int(course.get("nombre_partants")),
            is_quinte=bool(course.get("is_quinte")),
        )
        pmu_course = None
        pmu_parts: dict[int, dict[str, Any]] = {}
        if self.pmu:
            try:
                pmu_course = self.pmu.find_course(date, code)
                if pmu_course:
                    cond.pays = pmu_course.pays
                    cond.corde = pmu_course.corde
                    cond.parcours = pmu_course.parcours
                    cond.depart_type = pmu_course.depart.get("type")
                    cond.depart_infere = bool(pmu_course.depart.get("infere"))
                    cond.conditions_txt = pmu_course.conditions
                    if not cond.allocation and pmu_course.allocation:
                        cond.allocation = pmu_course.allocation
                    parts = self.pmu.participants(
                        date, pmu_course.reunion_path, pmu_course.course_path
                    )
                    pmu_parts = {p["num"]: p for p in parts if p.get("num") is not None}
                    sources["pmu"] = True
            except PmuError:
                pass

        # --- Marche (cotes) ---
        cotes_by_num = self._cotes_by_num(date, code)
        # --- D4 ---
        d4_by_num = self._d4_by_num(date, code)

        # --- Chevaux ---
        horses: list[HorseDossier] = []
        for p in partants:
            h = self._build_horse(p, pmu_parts, cotes_by_num, d4_by_num, code)
            if with_history and h.idcheval:
                try:
                    h.historique = self._build_history(
                        h.idcheval, date, code,
                        enrich=enrich_history and bool(self.pmu),
                        depth=enrich_depth,
                        nom=h.nom,
                    )
                except TurfBzhError:
                    pass
            if with_actor_stats:
                self._attach_actor_stats(h, cond.hippodrome)
            horses.append(h)

        # --- Renifleur (optionnel, souvent absent avant la course) ---
        renifleur = None
        try:
            renifleur = self.tb.course_renifleur(date, code)
        except TurfBzhError:
            pass

        return RaceDossier(
            conditions=cond, horses=horses, renifleur=renifleur, sources=sources
        )

    # ------------------------------------------------------------------ #
    def _build_horse(
        self,
        p: dict[str, Any],
        pmu_parts: dict[int, dict[str, Any]],
        cotes_by_num: dict[int, dict[str, Any]],
        d4_by_num: dict[int, dict[str, Any]],
        code: str,
    ) -> HorseDossier:
        num = H.safe_int(p.get("num"))
        pmu = pmu_parts.get(num, {})
        cote = cotes_by_num.get(num, {})
        d4 = d4_by_num.get(num, {})

        # --- Marche : derive les mouvements signes ---
        cote_val = H.safe_float(cote.get("cote"))
        cote_matin = H.safe_float(cote.get("cote_ref"))
        cote_5min = H.safe_float(cote.get("cote_5min_ago"))
        cote_bzh = H.safe_float(p.get("Cote_BZH"))
        drift_matin = None
        if cote_val is not None and cote_matin:
            drift_matin = (cote_val - cote_matin) / cote_matin * 100.0
        move_recent = None
        if cote_val is not None and cote_5min:
            move_recent = (cote_val - cote_5min) / cote_5min * 100.0
        ecart_value = None
        if cote_val is not None and cote_bzh:
            ecart_value = (cote_val - cote_bzh) / cote_bzh * 100.0

        partant_flag = p.get("partant")
        if partant_flag is None:
            partant_flag = str(p.get("Rank") or "").upper() != "NP"

        h = HorseDossier(
            num=num,
            nom=p.get("name") or pmu.get("nom"),
            idcheval=H.safe_int(p.get("idcheval")),
            age=H.safe_int(p.get("age")),
            sexe=p.get("Sexe"),
            partant=bool(partant_flag) and pmu.get("statut") != "NON_PARTANT",
            driver=p.get("jockey_driver") or pmu.get("driver"),
            entraineur=p.get("trainer") or pmu.get("entraineur"),
            proprietaire=p.get("owner") or pmu.get("proprietaire"),
            idjockey=H.safe_int(p.get("idjockey")),
            identraineur=H.safe_int(p.get("identraineur")),
            # engagement
            gains_totaux=H.safe_float(p.get("Gains_Totaux")),
            gains_course=H.safe_float(p.get("Gains_Course")),
            courses_courues=H.safe_int(p.get("Courses_courues")),
            nombre_victoire=H.safe_int(p.get("nombre_victoire")),
            nombre_place=H.safe_int(p.get("nombre_place")),
            imdc=H.safe_float(p.get("IMDC")),
            classe_groupe=p.get("classe_groupe"),
            alloc_precedente=H.safe_int(p.get("Alloc_Precedente")),
            rapport_alloc=H.safe_float(p.get("Rapport_Alloc")),
            moy_alloc=H.safe_int(p.get("Moy_Alloc")),
            handicap_distance=H.safe_int(pmu.get("handicap_distance")),
            # chronos
            record_distance_s=H.safe_float(p.get("distanceRecord_sec")),
            # forme
            taux_place=H.safe_float(p.get("Taux_Place")),
            taux_victoire=H.safe_float(p.get("Taux_Victoire")),
            taux_incident=H.safe_float(p.get("Taux_Incident")),
            turf_points=H.safe_int(p.get("Turf_Points")),
            tpch_90=H.safe_int(p.get("TPch_90")),
            moy_tpch_365=H.safe_int(p.get("Moy_TPch_365")),
            repos=H.safe_int(p.get("Repos")),
            ex_fav=p.get("ExFav"),
            popularite=H.safe_float(p.get("Popularite")),
            evo_popularite=H.safe_float(p.get("Evo_Popul")),
            musique=p.get("Musique") or pmu.get("musique"),
            musique_parsed=H.parse_musique(p.get("Musique") or pmu.get("musique")),
            evo_distance=p.get("Evo_Distance"),
            # ferrure
            ferrure=H.normalize_ferrure(p.get("ferrure") or pmu.get("deferre_raw")),
            ferrure_prec=p.get("ferrure_prec"),
            changement_ferrure=p.get("changement_ferrure"),
            d4_statut=d4.get("statut"),
            d4_risque_faute=d4.get("risque_faute"),
            # indicateurs acteurs
            elo_cheval=H.safe_float(p.get("ELO_Cheval")),
            sigma_horse=H.safe_float(p.get("Sigma_Horse")),
            elo_jockey=H.safe_float(p.get("ELO_Jockey")),
            elo_entraineur=H.safe_float(p.get("ELO_Entraineur")),
            rang_j=H.safe_int(p.get("Rang_J")),
            tpj_90=H.safe_int(p.get("TPJ_90")),
            synergie_jch=H.safe_float(p.get("Synergie_JCh")),
            # IA
            note_ia=H.safe_float(p.get("Note_IA_Decimale")),
            ia_gagnant=H.safe_float(p.get("IA_Gagnant")),
            cote_bzh=cote_bzh,
            # marche
            cote=cote_val,
            cote_matin=cote_matin,
            cote_tendance_pct=H.safe_float(cote.get("tendance_pct")),
            cote_drift_matin_pct=drift_matin,
            cote_move_recent_pct=move_recent,
            cote_dernier_mouvement=cote.get("dernier_mouvement"),
            ecart_value_pct=ecart_value,
            favori=bool(cote.get("favori")),
            # PMU
            oeilleres=pmu.get("oeilleres"),
            avis_entraineur=p.get("avis_entraineur") or pmu.get("avis_entraineur"),
            driver_change=pmu.get("driver_change"),
            allocation_jour=None,  # renseigne via conditions
            raw_turfbzh=p,
            raw_pmu=pmu,
        )
        return h

    # ------------------------------------------------------------------ #
    def _attach_actor_stats(self, h: HorseDossier, hippodrome: str | None) -> None:
        """Renseigne driver_stats / entraineur_stats (forme 90j + reussite hippodrome)."""
        if h.idjockey:
            try:
                st = self.tb.personne_stats(
                    "jockey", h.idjockey, periode_jours=90, discipline=DISCIPLINE_ATTELE
                )
                h.driver_stats = self._summarize_actor(st, hippodrome, "rides")
            except TurfBzhError:
                pass
        if h.identraineur:
            try:
                st = self.tb.personne_stats(
                    "entraineur", h.identraineur, periode_jours=90, discipline=DISCIPLINE_ATTELE
                )
                h.entraineur_stats = self._summarize_actor(st, hippodrome, "partants")
            except TurfBzhError:
                pass

    @staticmethod
    def _summarize_actor(
        st: dict[str, Any], hippodrome: str | None, n_key: str
    ) -> dict[str, Any]:
        if not isinstance(st, dict):
            return {}
        out: dict[str, Any] = {
            "win_rate": H.safe_float(st.get("win_rate")),
            "place_rate": H.safe_float(st.get("place_rate")),
            "n": H.safe_int(st.get(n_key)) or H.safe_int(st.get("rides")),
            "roi_place": H.safe_float(st.get("roi_place")),
        }
        target = H.norm_name(hippodrome)
        hippo_rides = hippo_wins = None
        rows = st.get("top_hippodromes") or []
        if isinstance(st.get("repartition_hippodromes"), dict):
            rows = rows + (st["repartition_hippodromes"].get("lignes") or [])
        for row in rows:
            if target and H.norm_name(row.get("hippodrome")) == target:
                hippo_rides = H.safe_int(row.get("rides"))
                hippo_wins = H.safe_int(row.get("wins"))
                break
        out["hippo_rides"] = hippo_rides
        out["hippo_wins"] = hippo_wins
        return out

    # ------------------------------------------------------------------ #
    def _cotes_by_num(self, date: str, code: str) -> dict[int, dict[str, Any]]:
        try:
            cc = self.tb.course_cotes(date, code)
        except TurfBzhError:
            return {}
        parts = cc.get("partants", []) if isinstance(cc, dict) else []
        out = {}
        for p in parts:
            n = H.safe_int(p.get("num"))
            if n is not None:
                out[n] = p
        return out

    def _d4_by_num(self, date: str, code: str) -> dict[int, dict[str, Any]]:
        try:
            d4 = self.tb.journee_d4(date)
        except TurfBzhError:
            return {}
        rows = d4 if isinstance(d4, list) else d4.get("data", []) if isinstance(d4, dict) else []
        out = {}
        for row in rows:
            if row.get("code_course") == code:
                n = H.safe_int(row.get("numero"))
                if n is not None:
                    out[n] = row
        return out

    # ------------------------------------------------------------------ #
    def _build_history(
        self,
        idcheval: int,
        race_date: str,
        race_code: str,
        *,
        enrich: bool,
        depth: int,
        nom: str | None = None,
    ) -> list[PastPerf]:
        raw = self.tb.cheval_historique(idcheval)
        items = raw.get("history", []) if isinstance(raw, dict) else []
        perfs: list[PastPerf] = []
        for it in items:
            # exclure la course du jour
            if it.get("date") == race_date and it.get("course") == race_code:
                continue
            rk = H.parse_rank(it.get("Rank"))
            perfs.append(
                PastPerf(
                    date=it.get("date"),
                    hippodrome=it.get("hippodrome"),
                    code_course=it.get("course"),
                    discipline=it.get("discipline"),
                    distance=H.safe_int(it.get("distance")),
                    allocation=H.safe_int(it.get("allocation")),
                    nombre_partants=H.safe_int(it.get("nombre_partants")),
                    place=rk["place"],
                    fault=rk["fault"],
                    raw_rank=rk["raw"],
                    earnings=H.safe_float(it.get("earnings")),
                    ferrure=H.normalize_ferrure(it.get("ferrure")),
                )
            )
        perfs.sort(key=lambda x: x.date or "", reverse=True)

        if enrich and self.pmu:
            for perf in perfs[:depth]:
                self._enrich_perf(perf, nom)
        return perfs

    def _enrich_perf(self, perf: PastPerf, nom: str | None = None) -> None:
        """Ajoute RK, corde, depart, commentaire d'une sortie passee via PMU."""
        if not (perf.date and perf.code_course and self.pmu):
            return
        try:
            pc = self.pmu.find_course(perf.date, perf.code_course)
            if not pc:
                return
            perf.corde = pc.corde
            perf.depart = pc.depart.get("type")
            parts = self.pmu.participants(perf.date, pc.reunion_path, pc.course_path)
            # match par nom (fiable) puis repli sur le rang d'arrivee
            target = H.norm_name(nom)
            match = None
            if target:
                for p in parts:
                    if H.norm_name(p.get("nom")) == target:
                        match = p
                        break
            if match is None and perf.place is not None:
                for p in parts:
                    if p.get("rang_arrivee") == perf.place:
                        match = p
                        break
            if match:
                perf.reduction_km_s = match.get("reduction_km_s")
                perf.commentaire = match.get("commentaire_course")
                perf.incident = match.get("incident")
                if match.get("deferre_raw") and not perf.ferrure:
                    perf.ferrure = H.normalize_ferrure(match.get("deferre_raw"))
        except PmuError:
            return
