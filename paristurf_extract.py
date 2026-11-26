"""Extraction de l'edition numerique Paris-Turf (contenu de l'abonne) vers JSON + Word.

Usage personnel : on ne scrape AUCUN site. L'outil traite uniquement les fichiers
que tu fournis toi-meme (PDF ou images de l'edition a laquelle tu es abonne), et
en restitue l'integralite du texte, page par page, pour tri ulterieur.

Installation des dependances :
    pip install -r requirements-paristurf.txt

Utilisation :
    # 1) Depose l'edition du jour dans un dossier, ex :
    #    inbox/2026-11-26/  (un gros PDF, plusieurs PDF, ou des images .png/.jpg)
    # 2) Lance :
    python paristurf_extract.py --in inbox/2026-11-26 --date 2026-11-26

    # Forcer l'OCR (si PDF scanne / pages en image) :
    python paristurf_extract.py --in inbox/2026-11-26 --date 2026-11-26 --ocr

Sorties :
    out/paristurf/2026-11-26.json   (texte integral structure page par page)
    out/paristurf/2026-11-26.docx   (document Word lisible)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
PDF_EXTS = {".pdf"}

# Lignes considerees comme des titres/sections (pour le confort de lecture).
HEADING_RE = re.compile(
    r"^(?:"
    r"PRIX\b|R[EÉ]UNION\b|R\d+\b|C\d+\b|LES\s+[A-ZÉÈ]|L'ACTU|PARIS\s*TURF|"
    r"[0-9]{1,2}(?:re|e|er)\s+COURSE|SONT\s+AU\s+D[EÉ]PART|FAITES\s+VOS\s+JEUX|"
    r"LES\s+PERFORMANCES|LES\s+AVANT[- ]PARTANTS|LES\s+R[EÉ]SULTATS|"
    r"CLASSEMENT\s+TH[EÉ]ORIQUE|RAPPORT\s+PROBABLE|LES\s+ENTRA[IÎ]NEURS"
    r")"
)
# Un titre est aussi une ligne courte en majuscules.
UPPER_RE = re.compile(r"^[A-ZÉÈÀÂÎÔÛÇ0-9][A-ZÉÈÀÂÎÔÛÇ0-9 '’\-°./()]+$")


@dataclass
class PageResult:
    index: int
    source_file: str
    method: str  # "pdf-text" | "ocr-image" | "ocr-pdf"
    text: str
    sections: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Decoupage leger en sections (titres) - niveau "texte integral"
# --------------------------------------------------------------------------- #
def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 70:
        return False
    if HEADING_RE.match(s):
        return True
    # ligne courte, essentiellement en capitales
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 3 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.85:
        return bool(UPPER_RE.match(s))
    return False


def split_sections(text: str) -> list[dict]:
    sections: list[dict] = []
    current = {"title": None, "lines": []}
    for raw in text.splitlines():
        line = raw.rstrip()
        if _is_heading(line):
            if current["title"] is not None or current["lines"]:
                sections.append(current)
            current = {"title": line.strip(), "lines": []}
        else:
            current["lines"].append(line)
    if current["title"] is not None or current["lines"]:
        sections.append(current)
    # nettoyage : texte joint
    out = []
    for sec in sections:
        body = "\n".join(l for l in sec["lines"]).strip()
        if sec["title"] or body:
            out.append({"title": sec["title"], "text": body})
    return out


# --------------------------------------------------------------------------- #
# OCR (optionnel, lazy)
# --------------------------------------------------------------------------- #
_OCR_ENGINE = None


def _get_ocr():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "OCR requis mais 'rapidocr-onnxruntime' n'est pas installe.\n"
                "Installe-le : pip install rapidocr-onnxruntime"
            ) from exc
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def ocr_image(path: str) -> str:
    engine = _get_ocr()
    result, _ = engine(path)
    if not result:
        return ""
    # result : liste de [box, texte, score] dans l'ordre de detection
    return "\n".join(item[1] for item in result)


# --------------------------------------------------------------------------- #
# Lecture des entrees
# --------------------------------------------------------------------------- #
def extract_pdf(path: Path, use_ocr: bool) -> list[PageResult]:
    import pymupdf  # PyMuPDF (ex-"fitz")

    pages: list[PageResult] = []
    doc = pymupdf.open(path)
    for i, page in enumerate(doc, 1):
        text = page.get_text("text") or ""
        method = "pdf-text"
        if use_ocr or len(text.strip()) < 20:
            # page sans vraie couche texte -> rendu + OCR
            try:
                pix = page.get_pixmap(dpi=300)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_path = tmp.name
                pix.save(tmp_path)
                ocr_text = ocr_image(tmp_path)
                Path(tmp_path).unlink(missing_ok=True)
                if len(ocr_text.strip()) > len(text.strip()):
                    text, method = ocr_text, "ocr-pdf"
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001
                print(f"  [warn] OCR page {i} de {path.name}: {exc}", file=sys.stderr)
        pages.append(PageResult(
            index=i, source_file=f"{path.name}#p{i}", method=method,
            text=text, sections=split_sections(text),
        ))
    doc.close()
    return pages


def extract_image(path: Path) -> PageResult:
    text = ocr_image(str(path))
    return PageResult(
        index=0, source_file=path.name, method="ocr-image",
        text=text, sections=split_sections(text),
    )


def collect_inputs(in_dir: Path) -> list[Path]:
    files = [p for p in sorted(in_dir.iterdir()) if p.is_file()]
    return [p for p in files if p.suffix.lower() in (PDF_EXTS | IMAGE_EXTS)]


# --------------------------------------------------------------------------- #
# Livrables
# --------------------------------------------------------------------------- #
def build_json(date: str, pages: list[PageResult]) -> dict:
    return {
        "date": date,
        "source": "Paris-Turf - edition numerique (abonne, usage personnel)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "nb_pages": len(pages),
        "pages": [
            {
                "index": p.index,
                "source_file": p.source_file,
                "method": p.method,
                "text": p.text,
                "sections": p.sections,
            }
            for p in pages
        ],
    }


def build_docx(date: str, pages: list[PageResult], out_path: Path) -> None:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.add_heading(f"Paris-Turf - edition du {date}", level=0)
    doc.add_paragraph(
        f"Document genere le {datetime.now():%Y-%m-%d %H:%M} - "
        f"{len(pages)} page(s) - usage personnel (abonne)."
    )
    for p in pages:
        doc.add_heading(f"Page {p.index} ({p.source_file}) - {p.method}", level=1)
        if not p.sections:
            doc.add_paragraph(p.text)
            continue
        for sec in p.sections:
            if sec["title"]:
                h = doc.add_paragraph()
                run = h.add_run(sec["title"])
                run.bold = True
                run.font.size = Pt(12)
            if sec["text"]:
                for line in sec["text"].splitlines():
                    if line.strip():
                        doc.add_paragraph(line)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extraction Paris-Turf -> JSON + Word.")
    ap.add_argument("--in", dest="in_dir", required=True,
                    help="Dossier contenant les fichiers du jour (PDF et/ou images).")
    ap.add_argument("--date", required=True, help="Date de l'edition, ex 2026-11-26.")
    ap.add_argument("--ocr", action="store_true",
                    help="Forcer l'OCR (PDF scanne / pages en image).")
    ap.add_argument("--out", default=None, help="Dossier de sortie (defaut out/paristurf).")
    args = ap.parse_args(argv)

    in_dir = Path(args.in_dir)
    if not in_dir.is_dir():
        print(f"Dossier introuvable : {in_dir}", file=sys.stderr)
        return 1
    inputs = collect_inputs(in_dir)
    if not inputs:
        print(f"Aucun PDF/image dans {in_dir}.", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else (ROOT / "out" / "paristurf")
    pages: list[PageResult] = []
    counter = 0
    for f in inputs:
        print(f"-> {f.name}")
        if f.suffix.lower() in PDF_EXTS:
            for pr in extract_pdf(f, use_ocr=args.ocr):
                counter += 1
                pr.index = counter
                pages.append(pr)
        else:
            pr = extract_image(f)
            counter += 1
            pr.index = counter
            pages.append(pr)

    data = build_json(args.date, pages)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{args.date}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    docx_path = out_dir / f"{args.date}.docx"
    build_docx(args.date, pages, docx_path)

    nb_text = sum(1 for p in pages if p.method == "pdf-text")
    nb_ocr = len(pages) - nb_text
    print(f"\nOK - {len(pages)} page(s) : {nb_text} via texte PDF, {nb_ocr} via OCR.")
    print(f"JSON -> {json_path}")
    print(f"Word -> {docx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
