"""
PDF and Image text extractor — multi-strategy with OCR fallback.

Strategy order:
  1. PyMuPDF embedded text  (fast, zero deps, works on digital PDFs)
  2. Tesseract OCR via pytesseract  (handles scanned PDFs and Images)
  3. Ollama vision model  (if strategy 1+2 fail)
"""

import io
import logging
import os
from contextlib import closing
import fitz  # PyMuPDF
from PIL import Image

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 30   # below this → treat page as image-only

# Tesseract configuration — bundled binary lives in <repo>/tesseract_bin/
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOCAL_TESS = os.path.join(_REPO_ROOT, "tesseract_bin", "tesseract.exe")
_SYSTEM_TESS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_LOCAL_TESSDATA = os.path.join(_REPO_ROOT, "tessdata")

# CRITICAL: the bundled tesseract.exe was compiled with a hardcoded build-time
# tessdata path that doesn't exist on user machines. Without TESSDATA_PREFIX,
# Tesseract crashes on first OCR call: "Error opening data file ...eng.traineddata".
# We set it here unconditionally so every Tesseract invocation works regardless
# of who imports text_extractor.py first.
if os.path.isdir(_LOCAL_TESSDATA) and not os.environ.get("TESSDATA_PREFIX"):
    os.environ["TESSDATA_PREFIX"] = _LOCAL_TESSDATA

try:
    import pytesseract
    if os.path.exists(_LOCAL_TESS):
        pytesseract.pytesseract.tesseract_cmd = _LOCAL_TESS
    elif os.path.exists(_SYSTEM_TESS):
        pytesseract.pytesseract.tesseract_cmd = _SYSTEM_TESS
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_text(file_path: str) -> str:
    """
    Extract text from a PDF or Image using the best available method.
    Always returns a string (may be empty if all strategies fail).
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == ".txt":
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    elif ext in [".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"]:
        return _extract_from_image(file_path)
    elif ext == ".pdf":
        return _extract_from_pdf(file_path)
    else:
        log.warning("unsupported extension %s for %s", ext, os.path.basename(file_path))
        return ""

def _extract_from_pdf(pdf_path: str) -> str:
    page_texts = []

    # closing() releases the native MuPDF handle (and its mmap) even if a
    # per-page get_text()/OCR call raises partway through the document.
    with closing(fitz.open(pdf_path)) as doc:
        for page_num, page in enumerate(doc):
            text = page.get_text().strip()

            if len(text) >= MIN_TEXT_CHARS:
                # Strategy 1: embedded text — done
                page_texts.append(text)
            else:
                # Page is image-only — try OCR
                log.debug("page %d has no embedded text -> trying OCR", page_num + 1)
                ocr_text = _ocr_page(page, page_num, pdf_path)
                page_texts.append(ocr_text)

    result = "\n".join(page_texts).strip()

    if result:
        log.info("extracted %d chars from %s", len(result), os.path.basename(pdf_path))
    else:
        log.warning("no text extracted from %s", os.path.basename(pdf_path))

    return result

def _extract_from_image(image_path: str) -> str:
    """Extract text from a standalone image file using Tesseract or Vision."""
    log.info("processing image %s", os.path.basename(image_path))
    try:
        with closing(fitz.open(image_path)) as doc:
            text = _ocr_page(doc[0], 0, image_path)

        if text:
            log.info("extracted %d chars from image %s", len(text), os.path.basename(image_path))
        else:
            log.warning("no text extracted from image %s", os.path.basename(image_path))

        return text
    except Exception as e:
        log.error("error processing image %s: %s", os.path.basename(image_path), e, exc_info=False)
        return ""

def is_image_pdf(pdf_path: str) -> bool:
    """Return True if the PDF appears to be entirely image-based (no embedded text)."""
    try:
        with closing(fitz.open(pdf_path)) as doc:
            total_text = "".join(page.get_text() for page in doc).strip()
        return len(total_text) < MIN_TEXT_CHARS
    except Exception:
        return True

# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — Tesseract OCR
# ─────────────────────────────────────────────────────────────────────────────

# TD6 fix: cache the probe result. Previously every page of a scanned PDF
# spawned `tesseract --version` to check availability — adding measurable
# overhead on long scanned documents. The result never changes within a
# process lifetime, so a module-level cache is safe.
_TESSERACT_AVAILABLE: bool | None = None


def _tesseract_available() -> bool:
    global _TESSERACT_AVAILABLE
    if _TESSERACT_AVAILABLE is None:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            _TESSERACT_AVAILABLE = True
        except Exception:
            _TESSERACT_AVAILABLE = False
    return _TESSERACT_AVAILABLE

def _has_arabic(text: str) -> bool:
    """True if `text` contains characters in the Arabic Unicode block."""
    if not text:
        return False
    for ch in text:
        cp = ord(ch)
        if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF:
            return True
    return False


def _ocr_with_tesseract(page: fitz.Page) -> str:
    """Render page to image and run Tesseract OCR on it.

    P2-F: First pass with fra+eng. If the result hints at Arabic content
    (Unicode range or low-yield page where the bundled `ara.traineddata`
    might fit better), re-run with ara+fra+eng for the Algerian market.
    """
    import pytesseract

    mat = fitz.Matrix(300 / 72, 300 / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img_bytes = pix.tobytes("png")

    # Keep the PIL image open for both OCR passes, then release its buffer via
    # the context manager regardless of which passes run or raise.
    with Image.open(io.BytesIO(img_bytes)) as img:
        try:
            text = pytesseract.image_to_string(img, lang="fra+eng")
        except Exception as e:
            # A missing fra/eng traineddata (or a transient tesseract error)
            # falls back to the default language — make the failure observable
            # rather than silently degrading OCR quality.
            log.warning("fra+eng OCR failed, retrying with default language: %s", e)
            text = pytesseract.image_to_string(img)

        text = (text or "").strip()
        if _has_arabic(text) or len(text) < 40:
            try:
                text_ar = pytesseract.image_to_string(img, lang="ara+fra+eng")
                if text_ar and (len(text_ar.strip()) > len(text) or _has_arabic(text_ar)):
                    log.info("Arabic detected -> ara+fra+eng OCR (%d chars)", len(text_ar))
                    return text_ar.strip()
            except Exception as e:
                log.warning("Arabic OCR fallback failed: %s", e)

    return text

# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3 — Ollama vision model
# ─────────────────────────────────────────────────────────────────────────────

VISION_MODELS = ["llama3.2-vision", "llava", "llava:13b", "minicpm-v", "moondream"]

def _ollama_vision_available() -> tuple[bool, str]:
    """Check if a vision-capable model is available in Ollama."""
    try:
        import requests
        # Resolve config safely (might be imported differently in new structure)
        # We will hardcode default URL but ideally pull from global config
        ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
        base = ollama_url.replace("/api/generate", "")
        
        resp = requests.get(f"{base}/api/tags", timeout=4)
        if not resp.ok:
            return False, ""
            
        available = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
        for vm in VISION_MODELS:
            if vm in available or any(vm in a for a in available):
                for a in resp.json().get("models", []):
                    if vm in a["name"]:
                        return True, a["name"]
        return False, ""
    except Exception:
        return False, ""

def _ocr_with_ollama_vision(page: fitz.Page, model: str) -> str:
    """Send page image to Ollama vision model for text extraction."""
    import requests, base64
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")

    mat = fitz.Matrix(200 / 72, 200 / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img_b64 = base64.b64encode(pix.tobytes("png")).decode()

    prompt = (
        "This is a page from a document. "
        "Extract ALL visible text exactly as written, preserving layout. "
        "Return only the extracted text, no commentary."
    )

    payload = {
        "model": model,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
        "options": {"temperature": 0.1},
    }

    try:
        resp = requests.post(ollama_url, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        log.warning("Ollama vision failed: %s", e)
        return ""

# ─────────────────────────────────────────────────────────────────────────────
# OCR dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def _ocr_page(page: fitz.Page, page_num: int, file_path: str) -> str:
    """Try available OCR strategies for a single image page."""
    
    if _tesseract_available():
        log.debug("using Tesseract OCR on page %d", page_num + 1)
        try:
            text = _ocr_with_tesseract(page)
            if text:
                return text
        except Exception as e:
            log.warning("Tesseract error on page %d: %s", page_num + 1, e)

    vision_ok, vision_model = _ollama_vision_available()
    if vision_ok:
        log.info("using Ollama vision (%s) on page %d", vision_model, page_num + 1)
        try:
            text = _ocr_with_ollama_vision(page, vision_model)
            if text:
                return text
        except Exception as e:
            log.warning("vision model error on page %d: %s", page_num + 1, e)

    log.warning("page %d: no OCR method available — page will be skipped", page_num + 1)
    return ""
