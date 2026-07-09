"""Regression tests locking in the audit fixes applied to the pipeline.

These cover pure-logic behaviour that broke silently before the fixes:
  - demurrage cost was zeroed for the entire first chargeable bracket
  - demurrage end-date used the first available date, not the earliest
  - MRZ 2-digit-year heuristic mis-assigned the century for dates of birth
  - MRZ name cleaner destroyed legitimate names made of filler letters
  - travel validation had no rules; travel projection was not idempotent

All tests are pure / use a throwaway sqlite file — no server, Ollama, or network.
"""
import os
import sys
from datetime import date

# Ensure repo root is importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.business.demurrage import calc_demurrage, demurrage_info
from core.extraction.mrz_extract import _yymmdd_to_iso, _clean_name
from core.validation.engine import validate_extraction


# ─── Demurrage: the critical zeroed-bracket bug ───────────────────────────────

def test_demurrage_first_chargeable_day_is_not_zero():
    """1 day over the free period must incur the tier-1 rate, not $0.

    Before the fix, tiers were in days-at-port units but called with
    days-over-free, so days 1-15 over free all returned $0.
    """
    assert calc_demurrage(1, "40 feet") == 40.0      # tier1 40ft rate
    assert calc_demurrage(1, "20 feet") == 20.0      # tier1 20ft rate
    assert calc_demurrage(15, "40 feet") == 15 * 40.0


def test_demurrage_zero_when_within_free_period():
    assert calc_demurrage(0, "40 feet") == 0.0
    assert calc_demurrage(-3, "40 feet") == 0.0


def test_demurrage_tier_boundaries():
    # 30 days over free = all in tier1 (1-30): 30 * 40
    assert calc_demurrage(30, "40 feet") == 30 * 40.0
    # 31 days = tier1 (30 days) + 1 day of tier2 (@ $80)
    assert calc_demurrage(31, "40 feet") == 30 * 40.0 + 1 * 80.0


def test_demurrage_end_date_uses_earliest_not_first():
    """Delivery earlier than restitution must win (earliest end date)."""
    info = demurrage_info(
        {"date_livraison": "2025-01-10", "date_restitution": "2025-06-01",
         "size": "40 feet"},
        {"eta": "2025-01-01", "compagnie_maritime": "CMA-CGM"},
    )
    # clock: 2025-01-01 -> earliest(delivery 01-10, restitution 06-01) = 01-10
    assert info["days_at_port"] == 9


def test_demurrage_document_free_days_override():
    info = demurrage_info(
        {"size": "40 feet"},
        {"eta": "2025-01-01", "compagnie_maritime": "CMA-CGM",
         "free_days": 5},
    )
    assert info["free_days"] == 5
    assert info["free_days_source"] == "document"


# ─── MRZ: century heuristic + name cleaning ───────────────────────────────────

def test_mrz_dob_future_maps_to_1900s():
    """A DOB whose 20YY reading is in the future must resolve to 19YY."""
    ref = date(2026, 7, 9)
    assert _yymmdd_to_iso("850312", role="dob", today=ref) == "1985-03-12"


def test_mrz_dob_past_stays_2000s():
    ref = date(2026, 7, 9)
    assert _yymmdd_to_iso("100615", role="dob", today=ref) == "2010-06-15"


def test_mrz_expiry_allows_future():
    ref = date(2026, 7, 9)
    assert _yymmdd_to_iso("300101", role="expiry", today=ref) == "2030-01-01"


def test_mrz_clean_name_preserves_real_names():
    assert _clean_name("KELLER") == "KELLER"
    assert _clean_name("MARX") == "MARX"
    # Filler runs between real names get stripped, real names kept
    assert _clean_name("AMMAR GGG KKK MELLAH") == "AMMAR MELLAH"


def test_mrz_clean_name_never_empties_a_real_name():
    """A name made entirely of filler letters is kept, not destroyed."""
    assert _clean_name("CECE") == "CECE"


# ─── Validation: travel rules now exist ───────────────────────────────────────

def test_travel_validation_flags_future_dob():
    res = validate_extraction({"dob": "2090-01-01", "document_number": "AB1234567",
                               "doc_type": "passport"}, module="travel")
    assert res["is_valid"] is False
    assert any(i["rule_id"] == "dob_in_past" for i in res["issues"])


def test_travel_validation_flags_expiry_before_issue():
    res = validate_extraction({"issue_date": "2020-01-01", "expiry_date": "2015-01-01",
                               "document_number": "AB1234567", "doc_type": "passport"},
                              module="travel")
    assert any(i["rule_id"] == "expiry_after_issue" for i in res["issues"])


def test_travel_validation_passes_clean_passport():
    res = validate_extraction({"dob": "1985-03-12", "issue_date": "2020-01-01",
                               "expiry_date": "2030-01-01", "document_number": "AB1234567",
                               "doc_type": "passport"}, module="travel")
    assert res["is_valid"] is True


# ─── Travel projection idempotency (needs a throwaway DB) ─────────────────────

def test_travel_projection_is_idempotent(tmp_path):
    from core.storage.db import init_schema, get_connection
    from core.api import projections

    db = str(tmp_path / "travel.db")
    init_schema(db)

    # Seed the parent `documents` row the projection's original_doc_id FKs to
    # (in production this is inserted at the storage step before projection).
    conn = get_connection(db)
    try:
        conn.execute("INSERT INTO documents (id, type, module) VALUES (101, 'PASSPORT', 'travel')")
        conn.commit()
    finally:
        conn.close()

    doc = {"full_name": "Jean Dupont", "dob": "1985-03-12", "nationality": "FRA",
           "document_type": "PASSPORT", "document_number": "P1234567"}

    r1 = projections.project("travel", db, 101, dict(doc))
    r2 = projections.project("travel", db, 101, dict(doc))

    assert r1.get("docs_inserted") == 1
    assert r2.get("docs_inserted") == 0, "re-projecting the same doc must not duplicate the row"

    conn = get_connection(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM documents_travel").fetchone()[0]
    finally:
        conn.close()
    assert n == 1
