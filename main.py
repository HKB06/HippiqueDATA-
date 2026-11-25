"""Point d'entree racine : `python main.py [options]`.

Ajoute src/ au chemin puis lance la CLI HippiquePredictor.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from hippique.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
