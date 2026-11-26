# 🐎 HippiquePredictor — méthode (trot attelé)

Analyse automatisée des courses de **trot attelé françaises** selon la méthode Pour chaque course, l'outil fournit :

1. **1 cheval recommandé**
2. **2 opposants**
3. un **indice de confiance /10**
4. une **explication synthétique**

Le moteur est **hybride** : un moteur de scoring déterministe en Python
implémente les 4 piliers et l'ensemble des critères de la méthode, et un export
« prompt V5 + dossier structuré » permet de confier l'analyse à une IA externe.

## Sources de données

- **turf.bzh** (API v1, licence Premium) — source principale : programme, ELO,
  note IA, indicateurs, historique, cotes en direct, stats driver/entraîneur.
- **PMU** (API publique non officielle) — complément : corde, type de départ,
  réduction kilométrique et commentaire de course (manière de finir), ferrure.

## Méthode (fidèle au prompt V5)

- **Piliers majeurs** : engagement · chronos (record + RK en conditions proches
  + RK dans les 3 premiers + rendement de distance) · forme (musique, manière de
  finir, chrono en progrès) · marché (baisse progressive vs tardive, écart à la
  Cote_BZH).
- **Très importants** : distance idéale · aptitude au parcours (conjoint :
  hippodrome + distance + corde + départ) · type de départ (autostart / volté).
- **Confirmation** : qualité de l'opposition · ferrure · récupération · retour
  après break · état de piste · profil de course.
- **Départage** : driver · entraîneur · association · niveau de l'épreuve.

Principes : analyse **comparative** au lot, aucune donnée isolée n'élimine un
cheval, une donnée manquante devient un critère **non évaluable** (jamais 0), et
la confiance monte avec la **convergence** de signaux indépendants. Le monté
n'est jamais mélangé à l'attelé (information complémentaire seulement).

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copier `.env.example` vers `.env` et renseigner la clé turf.bzh :

```
TURFBZH_API_KEY=tbz_live_xxxxxxxx
```

## Utilisation

### Application Streamlit (recommandé)

```powershell
.\.venv\Scripts\streamlit.exe run streamlit_app.py
```

Choix de la date, mode **course précise** ou **journée complète**, enrichissement
« smart » (profond en course unique, allégé en journée), verdict V5, classement
complet et dossier détaillé par cheval.

### Ligne de commande

```powershell
python main.py                          # tout le trot attelé FR du jour
python main.py --course R4C3            # une course précise
python main.py --date 2026-11-25 --course R4C3 --prompt
python main.py --hippodrome vincennes   # filtre hippodrome
python main.py --fast                   # survol rapide d'une journée
python main.py --json out/jour.json     # export JSON
```

Options principales : `--no-enrich`, `--no-actor-stats`, `--enrich-depth N`,
`--no-history`, `--no-france-filter`, `--prompt`.

## Déploiement en ligne (Streamlit Community Cloud)

L'app peut être publiée gratuitement et se met à jour à chaque `git push`.

1. Aller sur [share.streamlit.io](https://share.streamlit.io) et se connecter avec GitHub.
2. **Create app → Deploy a public app from GitHub** :
   - Repository : `HKB06/HippiqueDATA-`
   - Branch : `main`
   - Main file path : `streamlit_app.py`
3. **Advanced settings → Secrets** : coller (voir `.streamlit/secrets.toml.example`)
   ```toml
   TURFBZH_API_KEY = "tbz_live_xxxxxxxx"
   ```
4. **Deploy**. L'URL publique est de la forme `https://<nom>.streamlit.app`.

La clé API reste dans les *Secrets* (jamais dans le dépôt). En local, elle est
lue depuis `.env` ; en ligne, depuis les Secrets.

## Structure

```
src/hippique/
  config.py          # configuration (.env)
  clients/           # turfbzh.py, pmu.py, http_cache.py
  collect.py         # fusion turf.bzh + PMU -> dossier de course
  engine.py          # moteur de scoring V5 (4 piliers + critères)
  prompt_v5.py       # texte de la méthode + instructions données
  output.py          # rendu console / JSON / prompt IA
  cli.py             # interface ligne de commande
main.py              # point d'entrée CLI
streamlit_app.py     # application web
paristurf_extract.py # extraction édition Paris-Turf (abonné) -> JSON + Word
```

## Outil annexe : extraction de l'édition Paris-Turf (abonné)

`paristurf_extract.py` convertit l'édition numérique **que tu fournis toi-même**
(contenu payant auquel tu es abonné — **aucun scraping de site**) en
**JSON + Word**, texte intégral page par page.

```powershell
pip install -r requirements-paristurf.txt
# Dépose l'édition du jour dans inbox/2026-11-26/ (PDF ou images), puis :
python paristurf_extract.py --in inbox/2026-11-26 --date 2026-11-26
```

- **PDF de l'édition** → extraction du vrai texte, fiable (fortement recommandé).
- **Images / PDF scanné** → OCR automatique (qualité moindre ; inexploitable sur
  les tableaux denses à basse résolution — préférer le PDF).

Le dossier `inbox/` est ignoré par git (ne jamais committer le contenu payant).
