"""Domain projection layer.

The engine pipeline saves the LLM result into the generic `documents` table
(blob of JSON). The dashboards, however, query domain-specific tables
(`persons`, `documents_travel`, `families` for travel; `shipments`, `containers`
for logistics).

This module bridges that gap: after every successful extraction, project the
flat LLM dict into the domain tables. Without this, the dashboards stay empty
even though `documents` fills up.

Public API:
    project(module, db_path, doc_id, extracted_data) -> dict
        Returns a small summary like {"persons_inserted": 1, "docs_inserted": 1}
        Always swallows errors per row — the projection should never fail
        a successful extraction.

Note: this module reads the LIVE schema (which is a hybrid of legacy
logistics_app + engine init_schema). It tolerates missing optional columns
via INSERT-with-only-known-columns.
"""
from __future__ import annotations

import json
import os
import sqlite3

from core.normalization import name_normalize
from core.storage.db import get_connection


# ─── Travel projection ─────────────────────────────────────────────────────────
def _project_travel(conn: sqlite3.Connection, doc_id: int, d: dict) -> dict:
    """Project a travel-document extraction into persons + documents_travel.

    For passports / ID cards / visas, runs MRZ extraction directly on the
    source file and merges the deterministic MRZ values OVER the LLM dict —
    MRZ wins because it's checksum-validated. The LLM keeps any extra fields
    it found that MRZ doesn't cover (issue_date, place_of_birth, etc.).
    """
    # ── MRZ override (deterministic) ─────────────────────────────────────────
    src = d.get("_source_file")
    mrz_summary = None
    if src and os.path.isfile(src):
        try:
            from core.extraction.mrz_extract import extract_mrz
            mrz = extract_mrz(src, min_score=40)
            if mrz:
                mrz_summary = {
                    "score":   mrz["mrz_valid_score"],
                    "name":    mrz.get("full_name"),
                    "dob":     mrz.get("dob"),
                    "doc_no":  mrz.get("document_number"),
                }
                # MRZ wins for these fields where MRZ has it
                for k in ("document_type", "document_number", "full_name",
                          "given_names", "surname", "dob", "nationality",
                          "sex", "expiry_date", "issuing_country",
                          "mrz_line_1", "mrz_line_2"):
                    v = mrz.get(k)
                    if v not in (None, ""):
                        d[k] = v
        except Exception as e:
            mrz_summary = {"error": f"{type(e).__name__}: {e}"}

    full_name = (d.get("full_name") or "").strip() or None
    dob = (d.get("dob") or "").strip() or None
    nationality = (d.get("nationality") or "").strip() or None
    gender = (d.get("gender") or d.get("sex") or "").strip() or None
    normalized = name_normalize(full_name)

    # ── TD5 wire: fuzzy identity resolution ────────────────────────────────
    # Replace exact-string matching with the RapidFuzz-based engine. It
    # catches "Mohamed AbdelKader" == "Mohammed Abdul-Khader" cases that
    # would otherwise create silent duplicate person rows.
    #
    # Decision tree:
    #   AUTO_MERGED  → reuse the matched person_id
    #   REVIEW       → reuse the matched person_id BUT log a `matches` row for
    #                  operator confirmation. Avoids creating duplicates while
    #                  the operator is uncertain.
    #   NEW_IDENTITY → fall through to INSERT (new person)
    #
    # Falls back to legacy exact-match if the fuzzy engine raises for any
    # reason — the projection must never block a successful extraction.
    person_id = None
    match_status = None
    match_score = None
    inserted_person = 0

    if normalized:
        try:
            from core.matching import resolve_identity
            from core.schemas.person import Person

            # Pre-filter candidates by 4-char prefix to keep scoring O(1)
            # against typical caseload sizes. Without this we'd score every
            # row in `persons` for every upload.
            prefix = normalized[:4] if len(normalized) >= 4 else normalized
            cand_rows = conn.execute(
                """SELECT id, full_name, normalized_name, dob, nationality
                   FROM persons WHERE normalized_name LIKE ? LIMIT 100""",
                (prefix + "%",),
            ).fetchall()

            candidates = []
            for r in cand_rows:
                try:
                    candidates.append(Person(
                        id=r[0],
                        full_name=r[1] or "",
                        normalized_name=r[2] or "",
                        dob=r[3],
                        nationality=r[4],
                    ))
                except Exception:
                    # Malformed row in DB — skip without breaking the resolver
                    continue

            new_person = Person(
                full_name=full_name or normalized,
                normalized_name=normalized,
                dob=dob,
                nationality=nationality,
            )
            result = resolve_identity(new_person, candidates)
            match_status = result.status
            match_score = round(result.score, 4) if result.score else 0.0

            if result.status == "AUTO_MERGED" and result.matched_person_id:
                person_id = result.matched_person_id
            elif result.status == "REVIEW" and result.matched_person_id:
                # Probable match, but uncertain. Insert the incoming identity as
                # its own person row first so the `matches` row can capture BOTH
                # sides of the pair (entity_a = new identity, entity_b = matched
                # candidate). This keeps the incoming document attached to a
                # distinct row that an operator can confirm/merge or split later,
                # rather than silently folding it into the matched candidate.
                cur = conn.execute(
                    """INSERT INTO persons (full_name, normalized_name, dob, nationality, gender)
                       VALUES (?, ?, ?, ?, ?)""",
                    (full_name, normalized or None, dob, nationality, gender),
                )
                person_id = cur.lastrowid
                inserted_person = 1
                try:
                    conn.execute(
                        """INSERT INTO matches
                              (entity_a_id, entity_b_id, score, status)
                           VALUES (?, ?, ?, 'PENDING')""",
                        (person_id, result.matched_person_id, float(result.score)),
                    )
                except Exception:
                    # matches table missing or column mismatch — non-fatal
                    pass
        except Exception as fuzzy_err:
            # Fall back to the legacy exact-match path so projections never
            # become a blocker. Status string is captured for the summary.
            match_status = f"FUZZY_FALLBACK:{type(fuzzy_err).__name__}"

        # Legacy fallback path — runs only when fuzzy engine didn't pick a
        # match (e.g. NEW_IDENTITY decision, or fuzzy crashed).
        if person_id is None:
            match_sql = "SELECT id FROM persons WHERE normalized_name = ?"
            params: tuple = (normalized,)
            if dob:
                match_sql += " AND (dob = ? OR dob IS NULL)"
                params = (normalized, dob)
            row = conn.execute(match_sql, params).fetchone()
            if row:
                person_id = row[0]

    if person_id is None:
        cur = conn.execute(
            """INSERT INTO persons (full_name, normalized_name, dob, nationality, gender)
               VALUES (?, ?, ?, ?, ?)""",
            (full_name, normalized or None, dob, nationality, gender),
        )
        person_id = cur.lastrowid
        inserted_person = 1

    # Insert the document_travel row.
    doc_type = (d.get("document_type") or "UNKNOWN").strip().upper()
    doc_number = d.get("document_number") or d.get("doc_number") or None
    expiry = d.get("expiry_date") or d.get("expiry") or None
    mrz1 = d.get("mrz_line_1") or ""
    mrz2 = d.get("mrz_line_2") or ""
    mrz_raw = (mrz1 + ("\n" if mrz1 and mrz2 else "") + mrz2) or None

    # ── Idempotency guard (mirrors the logistics container guard) ──
    # Re-uploading the same document must not create a duplicate
    # documents_travel row. Key on a stable identity:
    #   (person_id, doc_type, doc_number) when a doc_number is present,
    #   otherwise fall back to the originating document id.
    if doc_number:
        existing = conn.execute(
            """SELECT id FROM documents_travel
               WHERE person_id = ? AND doc_type = ? AND doc_number = ?""",
            (person_id, doc_type, doc_number),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id FROM documents_travel WHERE original_doc_id = ?",
            (doc_id,),
        ).fetchone()

    inserted_doc = 0
    if existing is None:
        conn.execute(
            """INSERT INTO documents_travel
                    (person_id, family_id, doc_type, doc_number, expiry_date, mrz_raw, original_doc_id)
               VALUES (?, NULL, ?, ?, ?, ?, ?)""",
            (person_id, doc_type, doc_number, expiry, mrz_raw, doc_id),
        )
        inserted_doc = 1

    out = {"persons_inserted": inserted_person, "docs_inserted": inserted_doc, "person_id": person_id}
    if mrz_summary:
        out["mrz"] = mrz_summary
    if match_status:
        out["match"] = {"status": match_status, "score": match_score}
    return out


# ─── Logistics projection ──────────────────────────────────────────────────────
# The live data/logistics.db uses the LEGACY logistics_app schema for shipments
# (compagnie_maritime, item_description, source_file, document_type, status,
# created_at, modified_at) and containers (statut_container, ...). We INSERT
# with those column names so dashboards keep reading from the same tables.

def _project_logistics(conn: sqlite3.Connection, doc_id: int, d: dict) -> dict:
    """Project a logistics-document extraction into shipments + containers."""
    tan = d.get("tan_number") or d.get("tan") or None
    vessel = d.get("vessel_name") or d.get("vessel") or None
    carrier = d.get("shipping_company") or d.get("carrier") or None
    port = d.get("port") or None
    transitaire = d.get("transitaire") or None
    item = d.get("item_description") or d.get("item") or None
    etd = d.get("etd") or None
    eta = d.get("eta") or None
    doctype = (d.get("document_type") or "UNKNOWN").strip().upper()
    status = "BOOKED" if doctype == "BOOKING" else (
        "IN_TRANSIT" if doctype == "DEPARTURE" else "UNKNOWN"
    )

    # ── Find existing shipment by TAN (idempotent on re-extraction) ──
    shipment_id = None
    if tan:
        row = conn.execute("SELECT id FROM shipments WHERE tan = ?", (tan,)).fetchone()
        if row:
            shipment_id = row[0]

    inserted_shipment = 0
    if shipment_id is None:
        # The legacy `shipments` table has these columns (see migrated schema)
        cur = conn.execute(
            """INSERT INTO shipments
                  (tan, item_description, compagnie_maritime, port, transitaire,
                   vessel, etd, eta, document_type, status, source_file)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tan, item, carrier, port, transitaire, vessel, etd, eta,
             doctype, status, d.get("_source_file")),
        )
        shipment_id = cur.lastrowid
        inserted_shipment = 1

    # ── Insert each container ──
    inserted_containers = 0
    for c in (d.get("containers") or []):
        cn = (c.get("container_number") or "").strip() or None
        if not cn:
            continue
        # Skip if container already attached to this shipment
        existing = conn.execute(
            "SELECT id FROM containers WHERE shipment_id = ? AND container_number = ?",
            (shipment_id, cn),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """INSERT INTO containers
                  (shipment_id, container_number, size, seal_number, statut_container)
               VALUES (?, ?, ?, ?, ?)""",
            (shipment_id, cn, c.get("size"), c.get("seal_number"), "BOOKED"),
        )
        inserted_containers += 1

    return {
        "shipments_inserted":  inserted_shipment,
        "containers_inserted": inserted_containers,
        "shipment_id":         shipment_id,
    }


# ─── Public dispatcher ─────────────────────────────────────────────────────────
def project(module: str, db_path: str, doc_id: int,
            extracted_data: dict | str) -> dict:
    """Project a single extraction into domain tables. Never raises.

    extracted_data may be a dict OR a JSON string (the engine sometimes passes
    one, sometimes the other depending on whether you read it from the Job or
    from the documents table).
    """
    if isinstance(extracted_data, str):
        try:
            extracted_data = json.loads(extracted_data)
        except Exception:
            return {"error": "invalid extracted_data JSON"}

    if not isinstance(extracted_data, dict):
        return {"error": f"unsupported extracted_data type: {type(extracted_data).__name__}"}

    try:
        conn = get_connection(db_path)
        try:
            if module == "travel":
                summary = _project_travel(conn, doc_id, extracted_data)
            elif module == "logistics":
                summary = _project_logistics(conn, doc_id, extracted_data)
            else:
                return {"error": f"unknown module: {module}"}
            conn.commit()
            return summary
        finally:
            conn.close()
    except Exception as e:
        # Never let projection failures surface as job failures — extraction
        # already succeeded by the time we get here.
        return {"error": f"{type(e).__name__}: {e}"}
