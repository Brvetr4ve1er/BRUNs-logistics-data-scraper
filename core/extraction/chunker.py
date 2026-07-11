"""
core/extraction/chunker.py

Map-Reduce PDF chunking pipeline.

Problem: LLMs have a fixed context window (typically 8,192 tokens).
A 50-page PDF can contain 50,000+ tokens, causing truncation or crashes.

Solution:
  1. MAP   — Split the PDF into overlapping N-page chunks
  2. REDUCE — Merge all per-chunk JSON results into one unified record
             (later fields win on conflict, lists are appended)
"""

import fitz
import json
from contextlib import closing
from typing import Any

# Overlap between chunks to avoid losing data that straddles a page boundary
CHUNK_SIZE_PAGES = 5
OVERLAP_PAGES    = 1
# If extracted text is shorter than this threshold, skip chunking entirely
CHUNKING_THRESHOLD_CHARS = 3000

# Keys that identify the same real-world list item across overlapping chunks.
# Two container dicts with the same container_number are the same physical box
# even if one chunk saw fewer fields — they must be merged, not duplicated.
_LIST_IDENTITY_KEYS = ("container_number", "number")


def _identity_key(item: Any):
    """Return a stable identity for a list item, or None if it has no natural key."""
    if isinstance(item, dict):
        for k in _LIST_IDENTITY_KEYS:
            v = item.get(k)
            if v not in (None, ""):
                return (k, str(v).strip().upper())
    return None


def _merge_lists(base_list: list, update_list: list) -> list:
    """Combine two lists, folding items that share a natural identity key.

    Items with the same identity (e.g. same container_number) are deep-merged
    so a partial entry from an overlapping chunk enriches rather than duplicates
    the existing one. Items without an identity key fall back to exact-JSON
    dedup (the previous behaviour).
    """
    result: list = []
    index: dict = {}       # identity -> position in result
    seen_exact: set = set()
    for item in base_list + update_list:
        idk = _identity_key(item)
        if idk is not None:
            if idk in index:
                result[index[idk]] = _merge_dicts(result[index[idk]], item)
            else:
                index[idk] = len(result)
                result.append(item)
        else:
            token = json.dumps(item, sort_keys=True, default=str)
            if token not in seen_exact:
                seen_exact.add(token)
                result.append(item)
    return result


def _merge_dicts(base: dict, update: dict) -> dict:
    """
    Deep-merge two dicts:
    - Strings/numbers: non-null value from `update` wins
    - Lists: items are combined, folding list items that share an identity key
      (e.g. container_number) and exact-deduping the rest
    """
    merged = dict(base)
    for key, val in update.items():
        if val is None or val == "" or val == []:
            continue  # never overwrite with empty
        if key not in merged or merged[key] is None or merged[key] == "":
            merged[key] = val
        elif isinstance(val, list) and isinstance(merged[key], list):
            merged[key] = _merge_lists(merged[key], val)
        else:
            # Non-null update value wins (later chunks have more context)
            merged[key] = val
    return merged


def chunk_pdf_text(pdf_path: str) -> list[dict[str, Any]]:
    """
    Extract text per page-group from a PDF.
    Returns a list of chunk dicts: {"text": str, "pages": [int, ...]}
    """
    # closing() guarantees the native MuPDF handle (and its mmap'd file) is
    # released even if get_text()/load_page() raises partway through.
    with closing(fitz.open(pdf_path)) as doc:
        total_pages = len(doc)

        # For small documents, return a single chunk — no overhead
        full_text = ""
        for page in doc:
            full_text += page.get_text()

        if len(full_text) < CHUNKING_THRESHOLD_CHARS:
            return [{"text": full_text, "pages": list(range(1, total_pages + 1))}]

        # Large document — chunk it
        chunks = []
        step = CHUNK_SIZE_PAGES - OVERLAP_PAGES
        page_idx = 0

        while page_idx < total_pages:
            end_idx = min(page_idx + CHUNK_SIZE_PAGES, total_pages)
            chunk_text = ""
            page_nums = []
            for i in range(page_idx, end_idx):
                chunk_text += doc.load_page(i).get_text()
                page_nums.append(i + 1)
            if chunk_text.strip():
                chunks.append({"text": chunk_text, "pages": page_nums})
            page_idx += step

        return chunks


def merge_chunk_results(chunk_results: list[dict]) -> dict:
    """
    Reduce a list of per-chunk JSON dicts into one unified record.
    """
    if not chunk_results:
        return {}
    merged = {}
    for result in chunk_results:
        merged = _merge_dicts(merged, result)
    return merged
