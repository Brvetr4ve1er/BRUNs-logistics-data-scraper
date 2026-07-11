"""Regression tests for the second batch of audit fixes (pure-logic modules).

Covers: date century pivot + validity, identity-matching over-merge guard,
NL2SQL safety gate hardening, chunker container dedup + merge, and the
validity-aware confidence score. No server / Ollama / network required.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.normalization.dates import date_normalize
from core.business.nlsql import validate_select
from core.extraction.chunker import _merge_dicts, merge_chunk_results
from core.extraction.llm_client import _confidence
from core.matching import resolve_identity
from core.matching import thresholds
from core.schemas.person import Person


# ─── Date normalization ───────────────────────────────────────────────────────

def test_date_iso_passthrough_and_validation():
    assert date_normalize("2026-05-01") == "2026-05-01"
    assert date_normalize("2026-13-45") is None          # impossible date -> None
    assert date_normalize(None) is None
    assert date_normalize("garbage") is None


def test_date_two_digit_year_pivot():
    # Near-future logistics dates stay in the 2000s
    assert date_normalize("11-Mar-26") == "2026-03-11"
    # A DOB-style 2-digit year past the pivot goes to the 1900s (was 2085)
    assert date_normalize("12-Mar-85") == "1985-03-12"


def test_date_ddmmyyyy():
    assert date_normalize("05/03/2026") == "2026-03-05"


# ─── NL2SQL safety gate ───────────────────────────────────────────────────────

def test_nlsql_allows_plain_select_and_cte():
    ok, err, cleaned = validate_select("SELECT * FROM shipments;")
    assert ok and err is None and cleaned == "SELECT * FROM shipments"
    ok, _, _ = validate_select("WITH x AS (SELECT 1) SELECT * FROM x")
    assert ok


def test_nlsql_blocks_chaining_and_dml():
    assert validate_select("SELECT 1; DROP TABLE containers")[0] is False
    for kw in ("INSERT INTO t VALUES(1)", "UPDATE t SET a=1", "DELETE FROM t",
               "PRAGMA table_info(t)"):
        assert validate_select(kw)[0] is False


def test_nlsql_blocks_dangerous_functions_and_comments():
    assert validate_select("SELECT load_extension('x')")[0] is False
    assert validate_select("SELECT writefile('/tmp/x', 'y')")[0] is False
    assert validate_select("SELECT readfile('/etc/passwd')")[0] is False
    assert validate_select("SELECT 1 -- drop")[0] is False
    assert validate_select("SELECT 1 /* comment */ FROM t")[0] is False


# ─── Chunker merge ────────────────────────────────────────────────────────────

def test_merge_empty_and_scalar_precedence():
    assert merge_chunk_results([]) == {}
    # empty/null from a later chunk never clobbers an earlier real value
    assert _merge_dicts({"etd": "2026-01-01"}, {"etd": None})["etd"] == "2026-01-01"
    # a later non-empty scalar overwrites an earlier one
    assert _merge_dicts({"etd": "2026-01-01"}, {"etd": "2026-01-02"})["etd"] == "2026-01-02"


def test_merge_dedupes_containers_by_number():
    a = {"containers": [{"container_number": "MSCU1", "size": "40 feet"}]}
    b = {"containers": [{"container_number": "MSCU1", "seal_number": "S9"},
                        {"container_number": "MSCU2"}]}
    merged = _merge_dicts(a, b)
    nums = sorted(c["container_number"] for c in merged["containers"])
    assert nums == ["MSCU1", "MSCU2"]                    # MSCU1 not duplicated
    msc1 = next(c for c in merged["containers"] if c["container_number"] == "MSCU1")
    assert msc1["size"] == "40 feet" and msc1["seal_number"] == "S9"  # fields folded


# ─── Confidence scoring ───────────────────────────────────────────────────────

def test_confidence_full_logistics_is_one():
    data = {"tan_number": "TAN/1234/2026", "vessel_name": "MSC LAUREN",
            "etd": "2026-01-01", "eta": "2026-02-01", "shipping_company": "MSC",
            "containers": [{"container_number": "MSCU1234567"}]}
    assert _confidence("logistics", data) == 1.0


def test_confidence_ignores_empty_containers_and_bad_dates():
    data = {"tan_number": "TAN/1234/2026", "vessel_name": "MSC LAUREN",
            "etd": "not-a-date", "eta": "2026-02-01", "shipping_company": "MSC",
            "containers": [{}]}          # empty container + garbage etd
    # 6 fields, only tan/vessel/eta/company valid (4/6)
    assert _confidence("logistics", data) == round(4 / 6, 3)


# ─── Identity matching ────────────────────────────────────────────────────────

def _p(name, dob=None, nat=None, pid=None):
    return Person(id=pid, full_name=name, normalized_name=name.lower(), dob=dob, nationality=nat)


def test_matching_weights_sum_to_one():
    assert abs(thresholds.WEIGHT_NAME + thresholds.WEIGHT_DOB
               + thresholds.WEIGHT_NATIONALITY - 1.0) < 1e-9


def test_matching_new_identity_on_empty():
    r = resolve_identity(_p("john doe"), [])
    assert r.status == "NEW_IDENTITY" and r.score == 0.0


def test_matching_identical_auto_merges():
    cand = _p("mohamed ali", dob="1990-01-01", nat="DZA", pid=7)
    r = resolve_identity(_p("mohamed ali", dob="1990-01-01", nat="DZA"), [cand])
    assert r.status == "AUTO_MERGED" and r.matched_person_id == 7


def test_matching_does_not_over_merge_subset_name_without_corroboration():
    """A bare first name against a fuller name with no DOB/nat must NOT auto-merge."""
    cand = _p("john michael doe", pid=3)
    r = resolve_identity(_p("john"), [cand])
    assert r.status != "AUTO_MERGED"
