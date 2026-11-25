"""Application Streamlit HippiquePredictor - methode V5 (trot attele).

Lancement :
    streamlit run streamlit_app.py

Repond a l'ensemble du prompt V5 : pour chaque course, un cheval recommande,
2 opposants, un indice de confiance /10 et une explication synthetique, avec le
detail des 4 piliers et de tous les criteres, le dossier de chaque partant et
l'export du prompt + dossier JSON pour une IA externe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import json

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402


def _bridge_secrets() -> None:
    """En ligne (Streamlit Cloud), recopie les Secrets dans l'environnement pour
    que la configuration (qui lit os.environ / .env) fonctionne sans fichier .env."""
    try:
        for k in ("TURFBZH_API_KEY", "TURFBZH_BASE_URL", "PMU_ENABLED", "LETROT_ENABLED"):
            if not os.environ.get(k) and k in st.secrets:
                os.environ[k] = str(st.secrets[k])
    except Exception:  # noqa: BLE001 - pas de secrets en local : on ignore
        pass


_bridge_secrets()

from hippique.collect import Collector  # noqa: E402
from hippique.config import load_settings  # noqa: E402
from hippique.engine import V5Engine  # noqa: E402
from hippique.output import (  # noqa: E402
    analysis_to_dict,
    build_prompt_bundle,
    dossier_to_dict,
)

st.set_page_config(
    page_title="HippiquePredictor V5", page_icon="🐎", layout="wide"
)

# Regroupement des criteres selon la hierarchie du prompt V5.
CRIT_GROUPS = [
    ("Piliers majeurs", ["Engagement", "Chronos / reduction km", "Forme & preparation", "Marche / cote"]),
    ("Tres importants", ["Distance ideale", "Aptitude au parcours", "Type de depart"]),
    ("Confirmation", ["Qualite de l'opposition", "Ferrure", "Temps de recuperation", "Retour apres break", "Etat de la piste", "Profil de course"]),
    ("Departage", ["Driver", "Entraineur", "Association cheval/driver", "Niveau de l'epreuve"]),
]


# --------------------------------------------------------------------------- #
# Services (caches)
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner=False)
def get_collector():
    return Collector(load_settings())


@st.cache_resource(show_spinner=False)
def get_engine():
    return V5Engine()


@st.cache_data(show_spinner=False)
def get_races(date: str, france_only: bool):
    return get_collector().attele_races(date, france_only=france_only)


@st.cache_data(show_spinner=False)
def analyze(date, code, hippodrome, enrich, depth, actor, with_history):
    col, eng = get_collector(), get_engine()
    dossier = col.build_race(
        date, code,
        hippodrome=hippodrome or None,
        with_history=with_history,
        enrich_history=enrich,
        enrich_depth=depth,
        with_actor_stats=actor,
    )
    analysis = eng.analyze(dossier)
    return {
        "analysis": analysis_to_dict(analysis),
        "dossier": dossier_to_dict(dossier, with_history=True),
        "prompt": build_prompt_bundle(dossier),
    }


# --------------------------------------------------------------------------- #
# Helpers d'affichage
# --------------------------------------------------------------------------- #
def conf_color(conf: float) -> str:
    return "#1a9850" if conf >= 7 else "#f0ad4e" if conf >= 5 else "#d9534f"


def partants_by_num(res) -> dict:
    return {p["num"]: p for p in res["dossier"]["partants"] if p.get("num") is not None}


def evals_by_num(res) -> dict:
    return {e["num"]: e for e in res["analysis"]["classement"]}


def race_label(cond: dict) -> str:
    return (
        f"{cond['code_course']} · {cond.get('hippodrome') or '?'} · "
        f"{cond.get('distance') or '?'} m · {cond.get('heure') or '?'}"
    )


def render_conditions(cond: dict) -> None:
    corde = (cond.get("corde") or "").split("_")[-1].lower() or "?"
    dep = (cond.get("depart_type") or "?")
    if cond.get("depart_infere"):
        dep += " (infere)"
    bits = [
        f"**{cond.get('classe_groupe') or 'Course'}**",
        f"{cond.get('distance') or '?'} m",
        f"corde {corde}",
        f"depart {dep}",
        f"parcours {cond.get('parcours') or '-'}",
        f"alloc {cond.get('allocation') or '?'} €",
        f"{cond.get('nombre_partants') or '?'} partants",
    ]
    st.caption(" · ".join(bits))


def render_verdict(res) -> None:
    a = res["analysis"]
    cond = a["conditions"]
    rec = a["recommande"]
    conf = a["indice_confiance_sur_10"]
    parts = partants_by_num(res)

    st.markdown(f"### {race_label(cond)}")
    render_conditions(cond)

    if not rec:
        st.warning("Aucun partant exploitable.")
        return

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        p = parts.get(rec["num"], {})
        cote = p.get("cote")
        st.metric("1 · Recommande", f"#{rec['num']} {rec['nom']}",
                  f"cote {cote:g}" if cote else None)
    with c2:
        opp = a["opposants"]
        st.markdown("**2 · Opposants**")
        for o in opp:
            po = parts.get(o["num"], {})
            co = po.get("cote")
            st.markdown(f"#{o['num']} **{o['nom']}**" + (f"  ·  cote {co:g}" if co else ""))
    with c3:
        st.markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-size:13px;color:#888'>3 · Confiance</div>"
            f"<div style='font-size:40px;font-weight:700;color:{conf_color(conf)}'>{conf}</div>"
            f"<div style='color:#888'>/ 10</div></div>",
            unsafe_allow_html=True,
        )

    st.info("**4 · Explication** — " + a["explication"])


def ranking_table(res) -> pd.DataFrame:
    parts = partants_by_num(res)
    rows = []
    for e in res["analysis"]["classement"]:
        p = parts.get(e["num"], {})
        rows.append({
            "N°": e["num"],
            "Cheval": e["nom"],
            "Score": round(e["score"], 3),
            "Signaux": e["signaux_convergents"],
            "Cote": p.get("cote"),
            "Points forts": " ; ".join(e["positifs"][:2]),
            "Reserves": " ; ".join(e["penalisations"][:1]),
        })
    return pd.DataFrame(rows)


def horse_dossier(res, num: int) -> None:
    parts = partants_by_num(res)
    evs = evals_by_num(res)
    p = parts.get(num, {})
    e = evs.get(num, {})

    # Criteres regroupes selon la hierarchie du prompt
    crit_by_label = {c["critere"]: c for c in e.get("criteres", [])}
    for group, labels in CRIT_GROUPS:
        st.markdown(f"**{group}**")
        rows = []
        for lab in labels:
            c = crit_by_label.get(lab)
            if not c:
                continue
            rows.append({
                "Critere": lab,
                "Poids": c["poids"],
                "Score": "n/a" if c["score"] is None else round(c["score"], 2),
                "Analyse": c["commentaire"],
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # Dossier brut (champs cles)
    with st.expander("Donnees brutes du partant"):
        keys = [
            ("Gains totaux", p.get("gains_totaux")), ("Rapport alloc", p.get("rapport_alloc")),
            ("Record", p.get("record_km")), ("Musique", p.get("musique")),
            ("Taux place", p.get("taux_place")), ("Ferrure", p.get("ferrure")),
            ("Cote", p.get("cote")), ("Cote matin", p.get("cote_matin")),
            ("Drift matin %", p.get("cote_drift_matin_pct")),
            ("Mouvement 5 min %", p.get("cote_move_recent_pct")),
            ("Cote BZH", p.get("cote_bzh")), ("Note IA", p.get("note_ia")),
            ("Driver", p.get("driver")), ("Entraineur", p.get("entraineur")),
        ]
        st.table(pd.DataFrame(
            [{"Champ": k, "Valeur": v} for k, v in keys if v is not None]
        ))
        if p.get("driver_stats"):
            st.caption("Stats driver (90 j) : " + json.dumps(p["driver_stats"], ensure_ascii=False))
        if p.get("entraineur_stats"):
            st.caption("Stats entraineur (90 j) : " + json.dumps(p["entraineur_stats"], ensure_ascii=False))

    # Historique
    hist = p.get("historique") or []
    if hist:
        st.markdown("**Historique (attele = pilier ; monte = complement)**")
        hrows = []
        for h in hist[:12]:
            hrows.append({
                "Date": h.get("date"),
                "Hippo": h.get("hippodrome"),
                "Disc.": h.get("discipline"),
                "Dist.": h.get("distance"),
                "Place": h.get("raw_rank"),
                "RK": h.get("reduction_km"),
                "Corde": h.get("corde"),
                "Depart": h.get("depart"),
                "Ferrure": h.get("ferrure"),
                "Commentaire": (h.get("commentaire") or "")[:80],
            })
        st.dataframe(pd.DataFrame(hrows), hide_index=True, use_container_width=True)


def render_full_race(res) -> None:
    render_verdict(res)
    st.markdown("#### Classement complet")
    st.dataframe(ranking_table(res), hide_index=True, use_container_width=True)

    st.markdown("#### Dossier par cheval")
    parts = partants_by_num(res)
    rec = res["analysis"]["recommande"]
    order = [e["num"] for e in res["analysis"]["classement"]]
    for num in order:
        p = parts.get(num, {})
        star = " ⭐" if rec and num == rec["num"] else ""
        with st.expander(f"#{num} {p.get('nom') or '?'}{star}"):
            horse_dossier(res, num)

    cond = res["analysis"]["conditions"]
    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "⬇ Prompt V5 + dossier (IA)", res["prompt"],
            file_name=f"{cond['date']}_{cond['code_course']}_prompt.md",
            mime="text/markdown",
        )
    with d2:
        st.download_button(
            "⬇ Analyse JSON", json.dumps(res["analysis"], ensure_ascii=False, indent=2),
            file_name=f"{cond['date']}_{cond['code_course']}_analyse.json",
            mime="application/json",
        )


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
def main() -> None:
    st.title("🐎 HippiquePredictor — méthode V5")
    st.caption("Trot attelé · analyse comparative des 4 piliers et de tous les critères du prompt V5.")

    # Verification de la cle AVANT toute construction du client (evite un crash).
    settings = load_settings()
    if not settings.has_turfbzh_key:
        st.error(
            "**Clé API turf.bzh absente ou invalide.**\n\n"
            "En ligne (Streamlit Cloud) : ouvre **Manage app → Settings → Secrets** "
            "et ajoute :\n\n"
            "```toml\nTURFBZH_API_KEY = \"tbz_live_xxxxxxxx\"\n```\n\n"
            "En local : renseigne `TURFBZH_API_KEY` dans le fichier `.env` "
            "(format `tbz_live_...`)."
        )
        st.stop()

    with st.sidebar:
        st.header("Paramètres")
        date = st.date_input("Date", value=pd.Timestamp.today().date())
        date_iso = date.strftime("%Y-%m-%d")
        mode = st.radio("Mode", ["Course précise", "Journée complète"])
        france_only = st.checkbox("Trot attelé français uniquement", value=True)
        hippo = st.text_input("Filtre hippodrome (optionnel)")

        st.divider()
        st.subheader("Enrichissement « smart »")
        enrich = st.checkbox("Enrichissement PMU (RK + manière de finir)", value=True)
        actor = st.checkbox("Stats driver / entraîneur (turf.bzh)", value=True)
        with_history = st.checkbox("Charger l'historique", value=True)
        auto_depth = st.checkbox("Profondeur automatique", value=True,
                                 help="6 sorties en course unique, 3 en journée.")
        depth = None
        if not auto_depth:
            depth = st.slider("Profondeur d'enrichissement", 1, 8, 6)

    try:
        races = get_races(date_iso, france_only)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Impossible de récupérer le programme turf.bzh : {exc}")
        st.stop()
    if hippo:
        races = [r for r in races if hippo.lower() in str(r.get("hippodrome", "")).lower()]

    if not races:
        st.warning(f"Aucune course de trot attelé trouvée pour {date_iso}.")
        return

    st.success(f"{len(races)} course(s) de trot attelé le {date_iso}.")

    if mode == "Course précise":
        labels = {r["code_course"]: f"{r['code_course']} · {r.get('hippodrome','?')} · "
                  f"{r.get('heure','?')} · {r.get('distance','?')} m"
                  + (" · Quinté+" if r.get("is_quinte") else "")
                  for r in races}
        code = st.selectbox("Choisir une course", list(labels.keys()),
                            format_func=lambda c: labels[c])
        d = depth if depth is not None else 6
        if st.button("Analyser la course", type="primary"):
            try:
                with st.spinner("Collecte et analyse…"):
                    res = analyze(date_iso, code, hippo, enrich, d, actor, with_history)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Erreur pendant l'analyse de {code} : {exc}")
            else:
                render_full_race(res)

    else:  # Journée complète
        d = depth if depth is not None else 3
        st.caption("Astuce : la journée complète est plus rapide sans enrichissement profond.")
        if st.button("Analyser la journée", type="primary"):
            summary = []
            results = {}
            prog = st.progress(0.0)
            for i, r in enumerate(races, 1):
                code = r["code_course"]
                try:
                    res = analyze(date_iso, code, hippo, enrich, d, actor, with_history)
                    results[code] = res
                    a = res["analysis"]
                    rec = a["recommande"]
                    opp = a["opposants"]
                    summary.append({
                        "Course": code,
                        "Hippo": r.get("hippodrome"),
                        "Heure": r.get("heure"),
                        "Recommandé": f"#{rec['num']} {rec['nom']}" if rec else "-",
                        "Opposants": ", ".join(f"#{o['num']} {o['nom']}" for o in opp),
                        "Confiance": a["indice_confiance_sur_10"],
                    })
                except Exception as exc:  # noqa: BLE001
                    summary.append({"Course": code, "Hippo": r.get("hippodrome"),
                                    "Heure": r.get("heure"), "Recommandé": f"erreur: {exc}",
                                    "Opposants": "", "Confiance": None})
                prog.progress(i / len(races))
            prog.empty()

            st.markdown("### Synthèse de la journée")
            st.dataframe(pd.DataFrame(summary), hide_index=True, use_container_width=True)

            st.markdown("### Détail par course")
            for r in races:
                code = r["code_course"]
                if code not in results:
                    continue
                with st.expander(race_label(results[code]["analysis"]["conditions"])):
                    render_full_race(results[code])


if __name__ == "__main__":
    main()
