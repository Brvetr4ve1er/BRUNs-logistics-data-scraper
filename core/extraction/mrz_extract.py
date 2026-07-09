"""MRZ pre-extraction — deterministic ground truth for passports and IDs.

Every machine-readable travel document (passport, ID card, visa) has a
2-line or 3-line MRZ at the bottom. The MRZ encodes — with checksums —
surname, given names, document number, nationality, date of birth, sex,
expiry date, and issuing country. These fields are deterministic and far
more accurate than running Tesseract on the whole page and asking an LLM
to interpret the result.

This module wraps `passporteye.read_mrz` (which does its own OCR + image
processing internally) for both image and PDF inputs.

Public API:
    extract_mrz(file_path, min_score=40) -> dict | None
        Returns canonical fields if MRZ found with score >= min_score.
        Always returns full ISO dates (YYYY-MM-DD), not YYMMDD.
        Returns None if no MRZ found.

Output schema (all keys present, values may be None):
    {
        "mrz_valid_score":   int (0-100, passporteye's confidence),
        "mrz_line_1":        str,
        "mrz_line_2":        str,
        "document_type":     "PASSPORT" | "ID_CARD" | "VISA" | None,
        "document_number":   str,
        "surname":           str,
        "given_names":       str,
        "full_name":         str (surname + given_names),
        "dob":               "YYYY-MM-DD",
        "sex":               "M" | "F" | None,
        "nationality":       "ISO 3166-1 alpha-3",
        "expiry_date":       "YYYY-MM-DD",
        "issuing_country":   "ISO 3166-1 alpha-3",
    }
"""
from __future__ import annotations

import io
import os
import re
import warnings
from datetime import date, timedelta
from typing import Optional

# Suppress passporteye's deprecation noise (we don't own that library)
warnings.filterwarnings("ignore", category=FutureWarning, module=r"passporteye.*")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"skimage.*")


# ─── Rotation normalization ──────────────────────────────────────────────────
def _detect_rotation_osd(pil_image) -> int:
    """Use Tesseract's Orientation+Script Detection to determine rotation.

    Returns 0, 90, 180, or 270. Defaults to 0 on any error or low confidence.
    """
    try:
        import pytesseract
        osd = pytesseract.image_to_osd(pil_image, output_type=pytesseract.Output.DICT)
        rot = int(osd.get("rotate", 0) or 0)
        conf = float(osd.get("orientation_conf", 0) or 0)
        # OSD is unreliable below ~0.3 confidence — fall back to brute-force
        if conf < 0.3:
            return 0
        return rot if rot in (0, 90, 180, 270) else 0
    except Exception:
        return 0


def _img_to_bytes(pil_image) -> bytes:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return buf.getvalue()


def _preprocess_variants(pil_image):
    """Yield (label, image) tuples for image-quality variants worth trying.

    Order matters — the cheapest/most-common-helping variants come first so
    the early-exit at score >= 90 saves time.
    """
    try:
        from PIL import ImageOps, ImageFilter, ImageEnhance
    except ImportError:
        yield "raw", pil_image
        return

    yield "raw", pil_image
    gray = ImageOps.grayscale(pil_image)
    yield "autocontrast", ImageOps.autocontrast(gray, cutoff=2)
    yield "sharp+autoc",  ImageOps.autocontrast(gray.filter(ImageFilter.SHARPEN), cutoff=2)
    yield "contrast2x",   ImageEnhance.Contrast(gray).enhance(2.0)


def _try_all_rotations(pil_image, min_score: int = 0):
    """Find the best MRZ across all rotations × all preprocessing variants.

    Strategy:
        1. Try OSD-detected rotation × `raw` first (cheapest correct guess)
        2. Exit early if score >= 90
        3. Otherwise enumerate {0, 90, 180, 270} × {raw, autocontrast,
           sharp+autocontrast, contrast2x} and pick the highest score

    Returns (rotation_degrees_used, raw_dict_from_passporteye) or (None, None).
    """
    try:
        from passporteye import read_mrz
    except ImportError:
        return None, None

    def _score(image):
        try:
            m = read_mrz(_img_to_bytes(image))
        except Exception:
            return None
        if m is None:
            return None
        return m.to_dict()

    def _rotated(rot: int):
        if rot == 0:
            return pil_image
        # PIL rotates counter-clockwise; OSD reports clockwise.
        return pil_image.rotate(360 - rot, expand=True)

    # 1. OSD-guided fast path
    osd_rot = _detect_rotation_osd(pil_image)
    best_dict = _score(_rotated(osd_rot))
    best_rot = osd_rot if best_dict else None
    best_score = int(best_dict.get("valid_score", 0) or 0) if best_dict else 0
    if best_score >= 90:
        return best_rot, best_dict

    # 2. Full sweep: rotations × preprocessing
    rotations_to_try = [r for r in (0, 90, 180, 270) if r != osd_rot]
    rotations_to_try.insert(0, osd_rot)  # retry OSD with preprocessing too

    for rot in rotations_to_try:
        rotated = _rotated(rot)
        for label, variant in _preprocess_variants(rotated):
            if rot == osd_rot and label == "raw":
                continue  # already tried in fast path
            d = _score(variant)
            if not d:
                continue
            s = int(d.get("valid_score", 0) or 0)
            if s > best_score:
                best_dict, best_rot, best_score = d, rot, s
                if s >= 90:
                    return best_rot, best_dict  # early exit on great score

    if best_dict and best_score >= min_score:
        return best_rot, best_dict
    return None, None


def _yymmdd_to_iso(
    yymmdd: str,
    *,
    role: str = "expiry",
    future_window_years: int = 10,
    today: Optional[date] = None,
) -> Optional[str]:
    """Convert MRZ-style YYMMDD to ISO YYYY-MM-DD.

    The 2-digit year is disambiguated using the field's semantic role:
      - role="dob": a date of birth must be in the past, so 20YY is only
        chosen when it does not land in the future; otherwise 19YY.
      - role="expiry"/"issue": dates may be in the future, so prefer 20YY
        unless it lands implausibly far ahead (> future_window_years), in
        which case fall back to 19YY.

    `today` is injectable for deterministic testing. Returns None if the
    input doesn't parse cleanly.
    """
    if not yymmdd or len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    try:
        yy = int(yymmdd[0:2])
        mm = int(yymmdd[2:4])
        dd = int(yymmdd[4:6])
        if not (1 <= mm <= 12 and 1 <= dd <= 31):
            return None
        ref = today or date.today()
        candidate_2k = date(2000 + yy, mm, dd)
        if role == "dob":
            # A DOB cannot be in the future: if 20YY is still ahead of today,
            # the person must have been born in the 1900s.
            if candidate_2k > ref:
                return f"19{yy:02d}-{mm:02d}-{dd:02d}"
            return f"20{yy:02d}-{mm:02d}-{dd:02d}"
        # expiry/issue: future is allowed, but cap the plausible range.
        cutoff = ref + timedelta(days=365 * future_window_years)
        if candidate_2k > cutoff:
            return f"19{yy:02d}-{mm:02d}-{dd:02d}"
        return f"20{yy:02d}-{mm:02d}-{dd:02d}"
    except ValueError:
        return None


def _doc_type_from_mrz(mrz_type: str | None) -> Optional[str]:
    """Map passporteye's `type` ('P<', 'I<', 'V<', etc.) to our doc_type."""
    if not mrz_type:
        return None
    t = mrz_type[0].upper()
    return {"P": "PASSPORT", "I": "ID_CARD", "A": "ID_CARD",
            "C": "ID_CARD", "V": "VISA"}.get(t)


def _clean_name(s: str | None) -> Optional[str]:
    """Strip MRZ filler chars and OCR garbage tokens, normalize whitespace.

    The MRZ pads names with `<` characters, which we replace with spaces. But
    Tesseract often misreads the filler as `K`, `X`, `G`, `C` etc., producing
    bogus middle-name tokens like `ABDELHAKIM K KK MEBARKIA` or
    `AMMAR GGG KKK MELLAH`. We strip any token that's 1-4 characters long and
    contains only those filler letters — real names won't match the pattern.
    """
    if not s:
        return None
    tokens = s.replace("<", " ").split()
    # Drop only tokens that look like MRZ filler: 2-4 chars made ENTIRELY of the
    # filler letters Tesseract confuses `<` with. Bounding the length to 2-4
    # (per this function's contract) protects legitimate names — a single-char
    # initial is kept, and a 5+ char token is assumed real. As a safety net we
    # never strip every token: if the filter would empty the name, keep the
    # originals so a real name made of these letters isn't destroyed.
    _FILLER = set("KXGCE")
    kept = [t for t in tokens if not (2 <= len(t) <= 4 and set(t) <= _FILLER)]
    if not kept:
        kept = tokens
    cleaned = " ".join(kept).strip()
    return cleaned or None


def _build_canonical(raw: dict, rotation: int = 0) -> Optional[dict]:
    """Convert passporteye's raw dict into our canonical schema."""
    score = int(raw.get("valid_score", 0) or 0)
    raw_text = raw.get("raw_text") or ""
    mrz_lines = raw_text.split("\n") if raw_text else []
    return {
        "mrz_valid_score":  score,
        "mrz_rotation":     rotation,
        "mrz_line_1":       mrz_lines[0] if mrz_lines else "",
        "mrz_line_2":       mrz_lines[-1] if len(mrz_lines) > 1 else "",
        "mrz_raw":          raw_text,
        "document_type":    _doc_type_from_mrz(raw.get("type")),
        "document_number":  (raw.get("number") or "").strip("<") or None,
        "surname":          _clean_name(raw.get("surname")),
        "given_names":      _clean_name(raw.get("names")),
        "full_name":        _build_full_name(raw.get("surname"), raw.get("names")),
        "dob":              _yymmdd_to_iso(raw.get("date_of_birth", ""), role="dob"),
        "sex":              _normalize_sex(raw.get("sex")),
        "nationality":      _normalize_country(raw.get("nationality")),
        "expiry_date":      _yymmdd_to_iso(raw.get("expiration_date", "")),
        "issuing_country":  _normalize_country(raw.get("country")),
    }


def _read_mrz_from_bytes(img_bytes: bytes, min_score: int) -> Optional[dict]:
    """Run passporteye on raw image bytes — tries all rotations to handle
    photos taken sideways or upside-down. Returns canonical dict or None."""
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        pil_img = Image.open(io.BytesIO(img_bytes))
    except Exception:
        return None

    rotation, raw = _try_all_rotations(pil_img, min_score=0)
    if not raw:
        return None
    score = int(raw.get("valid_score", 0) or 0)
    if score < min_score:
        return None
    return _build_canonical(raw, rotation=rotation or 0)


def _build_full_name(surname: str | None, given: str | None) -> Optional[str]:
    s = _clean_name(surname)
    g = _clean_name(given)
    if s and g:
        return f"{g} {s}"
    return s or g


def _normalize_sex(s: str | None) -> Optional[str]:
    if not s:
        return None
    s = s.upper()
    return s if s in ("M", "F") else None  # OCR errors like 'Z', 'N' → drop


def _normalize_country(c: str | None) -> Optional[str]:
    """Return only valid 3-letter codes; OCR'd garbage like 'AAK' becomes None."""
    if not c:
        return None
    c = c.upper().strip("<")
    if len(c) == 3 and c.isalpha():
        return c
    return None


# ─── Public API ──────────────────────────────────────────────────────────────
def extract_mrz(file_path: str, min_score: int = 40) -> Optional[dict]:
    """Extract MRZ fields from an image or PDF file.

    For PDFs, every page is tried; the first one with a valid MRZ wins.
    Returns None if no MRZ was found at min_score or above.
    """
    if not os.path.isfile(file_path):
        return None

    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".pdf",):
        return _try_pdf(file_path, min_score)
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
        with open(file_path, "rb") as f:
            return _read_mrz_from_bytes(f.read(), min_score)
    return None


def _try_pdf(pdf_path: str, min_score: int) -> Optional[dict]:
    try:
        import fitz
    except ImportError:
        return None

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return None

    best = None
    try:
        for page in doc:
            mat = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            result = _read_mrz_from_bytes(pix.tobytes("png"), min_score=0)
            if result and (best is None or
                           result["mrz_valid_score"] > best["mrz_valid_score"]):
                best = result
    finally:
        doc.close()

    if best and best["mrz_valid_score"] >= min_score:
        return best
    return None
