"""Demurrage & Detention cost calculation with tiered carrier rate tables.

Pure functions. Inputs are dicts (container row + shipment row). Outputs are
small dicts shaped for the container detail template + swimlane card.

The rate tables come from a real CMA-CGM Bill of Lading clause (BL CFA0869742)
— see docs/02-HOW-IT-WORKS.md for the full table breakdown. Other carriers
fall back to the same brackets.

Free-days resolution (N5 wired):
    1. shipment["free_days"]  — explicit override from extracted_json
    2. DEFAULT_FREE_DAYS[carrier]
    3. DEFAULT_FREE_DAYS["default"]

Public API:
    calc_demurrage(days_over_free, container_size, tiers=None) -> float (USD)
    demurrage_info(container, shipment) -> dict with risk_level, costs, days
    free_days_from_documents(db_path, tan) -> int | None
    DEFAULT_FREE_DAYS, CMA_CGM_TIERS, DEFAULT_TIERS  (data tables)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date

from core.storage.db import get_connection

log = logging.getLogger(__name__)

# Default carrier free days (override with extracted clause when available — N5).
DEFAULT_FREE_DAYS: dict[str, int] = {
    "CMA-CGM": 15, "MSC": 14, "Maersk": 12, "default": 14,
}

# CMA-CGM tiered demurrage rates from real BL CFA0869742 (USD/day per size).
# Each tuple is (start_day, end_day, rate_20ft, rate_40ft).
#
# CONVENTION: brackets are expressed in DAYS-OVER-FREE units (free days already
# subtracted), NOT absolute days-at-port. calc_demurrage() is always called with
# days_over_free, and free_days varies per carrier (CMA-CGM=15, MSC=14,
# Maersk=12), so embedding a fixed 15-day free period in the brackets would be
# wrong for every other carrier. The original BL table was absolute (16-45,
# 46-60, 61-90, 91-999 days-at-port); it is remapped here by subtracting the
# CMA-CGM 15 free days so day 1 over free maps to the first chargeable bracket:
#   abs 16-45  -> over-free 1-30
#   abs 46-60  -> over-free 31-45
#   abs 61-90  -> over-free 46-75
#   abs 91-999 -> over-free 76-984
# $/day rates are unchanged.
CMA_CGM_TIERS: list[tuple[int, int, float, float]] = [
    (1,  30,  20.0,  40.0),   # 1-30 days over free:  $20/20ft, $40/40ft
    (31, 45,  40.0,  80.0),   # 31-45 days over free: $40/20ft, $80/40ft
    (46, 75,  60.0, 110.0),   # 46-75 days over free: $60/20ft, $110/40ft
    (76, 984, 80.0, 140.0),   # 76+ days over free:   $80/20ft, $140/40ft
]
DEFAULT_TIERS = CMA_CGM_TIERS  # fallback for other carriers

# Business policy constants (promoted from inline magic numbers).
DEFAULT_USD_TO_DZD = 135.0      # default exchange rate when none recorded
RISK_DAYS_CRITICAL = 30         # days_over_free > this => critical
RISK_DAYS_HIGH = 14             # days_over_free > this => high
RISK_DAYS_LOW_REMAINING = 3     # days_remaining <= this (and not over) => low


def calc_demurrage(days_over_free: int, container_size: str,
                   tiers: list | None = None) -> float:
    """Compute USD demurrage cost using tiered rates.

    Stops accumulating as soon as the bracket start exceeds days_over_free.
    Returns 0 when there's no overage (days_over_free <= 0).
    """
    if days_over_free <= 0:
        return 0.0
    tiers = tiers or DEFAULT_TIERS
    is_40 = not (container_size or "").lower().startswith("20")
    cost = 0.0
    for (start, end, rate20, rate40) in tiers:
        if days_over_free < start:
            break
        applicable_days = min(days_over_free, end) - start + 1
        rate = rate40 if is_40 else rate20
        cost += applicable_days * rate
    return round(cost, 2)


def demurrage_info(container: dict, shipment: dict) -> dict:
    """Compute demurrage risk info for a single container.

    The clock STARTS on `shipment.eta` (estimated arrival) and STOPS at the
    earliest of: actual restitution, delivery, or today. This is a known
    approximation — the correct clock-start is the carrier's Arrival Notice
    date, which the ARRIVAL_NOTICE prompt would extract (N6).

    Free-days resolution (N5 wire):
        1. shipment["free_days"]  — explicit override (parsed from the BL's
           demurrage_terms by the LLM prompt; takes precedence over defaults
           because it reflects the actual contract on this shipment)
        2. DEFAULT_FREE_DAYS[carrier]
        3. DEFAULT_FREE_DAYS["default"] (14 days)

    Returns a dict with:
        days_at_port, free_days, days_over_free, days_remaining,
        cost_usd, cost_dzd, risk_level (none|low|medium|high|critical),
        eta (echo of input), free_days_source ('document' | 'carrier' | 'default').
    """
    carrier = (shipment.get("compagnie_maritime") or "").strip()

    # Try the document-extracted value first; fall back to carrier defaults.
    free_days_source = "default"
    free_days = DEFAULT_FREE_DAYS["default"]
    extracted_fd = shipment.get("free_days")
    if extracted_fd is not None:
        try:
            fd = int(extracted_fd)
            if fd > 0:
                free_days = fd
                free_days_source = "document"
        except (ValueError, TypeError):
            pass
    if free_days_source == "default" and carrier in DEFAULT_FREE_DAYS:
        free_days = DEFAULT_FREE_DAYS[carrier]
        free_days_source = "carrier"

    eta_str = shipment.get("eta")
    date_restitution = container.get("date_restitution")
    date_livraison = container.get("date_livraison")

    # Bail early if we can't anchor the clock — return a "none" risk dict.
    if not eta_str:
        return {
            "days_at_port": None, "free_days": free_days,
            "free_days_source": free_days_source,
            "days_over_free": 0, "cost_usd": 0.0, "cost_dzd": 0.0,
            "risk_level": "none", "countdown": None,
        }
    try:
        eta = date.fromisoformat(eta_str)
    except ValueError:
        return {
            "days_at_port": None, "free_days": free_days,
            "free_days_source": free_days_source,
            "days_over_free": 0, "cost_usd": 0.0, "cost_dzd": 0.0,
            "risk_level": "none", "countdown": None,
        }

    # End date: earliest of actual restitution, delivery, or today.
    # Fold min() over BOTH dates — a later restitution must not hide an earlier
    # delivery. Per-date try/except so one bad string doesn't drop the other.
    end_date = date.today()
    for d_str in (date_restitution, date_livraison):
        if d_str:
            try:
                end_date = min(end_date, date.fromisoformat(d_str))
            except ValueError:
                pass

    days_at_port = max(0, (end_date - eta).days)
    days_over_free = max(0, days_at_port - free_days)
    days_remaining = free_days - days_at_port  # negative = already over

    taux = float(container.get("taux_de_change") or 0) or DEFAULT_USD_TO_DZD
    size = container.get("size") or ""
    cost_usd = calc_demurrage(days_over_free, size)
    cost_dzd = round(cost_usd * taux, 0)

    # Risk level — same buckets used by the dashboard action panel.
    if days_over_free > RISK_DAYS_CRITICAL:
        risk_level = "critical"
    elif days_over_free > RISK_DAYS_HIGH:
        risk_level = "high"
    elif days_over_free > 0:
        risk_level = "medium"
    elif days_remaining <= RISK_DAYS_LOW_REMAINING:
        risk_level = "low"
    else:
        risk_level = "none"

    return {
        "days_at_port":     days_at_port,
        "free_days":        free_days,
        "free_days_source": free_days_source,
        "days_over_free":   days_over_free,
        "days_remaining":   days_remaining,
        "cost_usd":         cost_usd,
        "cost_dzd":         cost_dzd,
        "risk_level":       risk_level,
        "eta":              eta_str,
    }


# ─── N5: free_days from extracted documents ────────────────────────────────

def free_days_from_documents(db_path: str, tan: str | None) -> int | None:
    """Look up the most recent extracted `free_days` value for a TAN.

    The v2.0 logistics prompt parses the demurrage clause from BL/Booking
    documents and stores `free_days` (an integer) at the top level of
    extracted_json. This function searches the documents table for the most
    recent entry matching `tan` that has a usable free_days field.

    Returns the integer or None if not found / DB missing / TAN empty.
    Errors are swallowed — demurrage_info() will fall back to the carrier
    default. Logged as debug because it's an expected miss for old documents
    extracted with the v1.x prompt.
    """
    if not tan or not os.path.exists(db_path):
        return None
    conn = get_connection(db_path)
    try:
        # Push the TAN into SQL so we never silently skip a matching doc that
        # falls outside an arbitrary recent-N window. The LIKE filters still
        # narrow the scan to logistics docs that mention free_days AND this TAN;
        # the Python guard below keeps an exact tan_number match.
        rows = conn.execute(
            """SELECT extracted_json FROM documents
               WHERE module = 'logistics'
                 AND extracted_json LIKE '%free_days%'
                 AND extracted_json LIKE ?
               ORDER BY id DESC""",
            (f'%"tan_number": "{tan}"%',),
        ).fetchall()
    finally:
        conn.close()

    for r in rows:
        try:
            ed = json.loads(r[0] or "{}")
        except json.JSONDecodeError:
            continue
        if ed.get("tan_number") != tan:
            continue
        fd = ed.get("free_days")
        if fd is None:
            continue
        try:
            return int(fd)
        except (ValueError, TypeError):
            log.debug("free_days for %s not parseable: %r", tan, fd)
            continue
    return None
