# 09 — Roadmap and Improvements

This is the engineering roadmap and a candid critique of what's currently
weak. Read it as a working document, not a marketing roadmap — items are
ordered by honest priority, not by what sells.

---

## Done

### ✅ Streamlit → Flask + HTMX + DaisyUI migration

`START_APP.bat` starts the Flask server on port 7845. Streamlit is fully
removed. All HTML routes, job tracking, upload + polling, edit forms, export,
settings, and LLM config modal are in `core/api/server.py` + `core/api/templates/`.

### ✅ Flask BI bridge (Power BI REST)

`/api/logistics/shipments_full` is live. Power BI connects in 3 clicks.

### ✅ Operator action center (P1-A)

4-card action strip on the dashboard: review queue count, D&D at-risk
containers, data gaps, on-track count. Each card links to a pre-filtered view.

### ✅ Three-tier confidence routing (P1-B)

Documents routed by confidence: ≥ 0.90 auto-approved, 0.60–0.89 review queue,
< 0.60 needs attention. The `/logistics/review` queue surfaces the specific
low-confidence field and score. Operators can approve from the queue.

### ✅ Re-extract with before/after diff (P1-C)

"Re-extract" button on document detail runs the current prompt on the stored
source file and shows a three-column diff (added / changed / removed). Nothing
is saved until the operator accepts. Implemented as a modal with HTMX.

### ✅ Activity feed from audit_log (P1-D)

Last 20 audit actions rendered on the logistics dashboard sidebar.
`audit_log` table fully populated by uploads, edits, and review approvals.

### ✅ URL-based filter state + share button (P1-E)

All dashboard/sheet/document-list filters are GET params. A "🔗 Share" button
copies the current URL. Colleagues open the same filtered view instantly.

### ✅ Bounding box field visualization (P2-A)

Source PDF rendered with PyMuPDF highlight annotations showing where each
extracted value was found. Color-coded by field type (TAN = blue, container =
green, vessel = orange, etc.). Clicking any field label on the right panel
highlights it on the left PDF. Page navigation included.

### ✅ Natural-language query — "Ask your data" (P2-B)

Search bar on the logistics dashboard accepts French or English plain text.
Ollama converts it to a SQLite SELECT, which runs locally. Results shown as a
table with the SQL disclosed below it. Safety gate blocks non-SELECT responses.

### ✅ Demurrage & Detention calculator (P2-C)

Per-container D&D risk level and USD/DZD cost estimate. Tiered rate tables
derived from a real CMA-CGM BL clause (15 free days, then $20–$110/day
depending on size and bracket). Progress bar in container detail view.
Risk color-coding in swimlane cards.

### ✅ Cross-document reconciliation (P2-D)

Document detail page queries all other documents sharing the same TAN and
flags: vessel name inconsistency, ETD/ETA divergence, container numbers absent
in some docs, gross weight difference > 5%. Discrepancies shown with per-doc
values and severity (warning / error).

### ✅ Container status swimlane (P2-E)

Visual pipeline at `/logistics/swimlane` grouped by status column
(BOOKED → IN_TRANSIT → ARRIVED → DELIVERED → RESTITUTED → CLOSED).
D&D risk shown as card border color. Clicking a card goes to container detail.

### ✅ Arabic OCR fallback (P2-F)

`core/extraction/text_extractor.py` detects Arabic Unicode in the first OCR
pass (fra+eng) and re-runs with `ara+fra+eng` if Arabic characters are found
or yield is below 40 characters. Bundled `tessdata/ara.traineddata` is used.

### ✅ Semantic document search via ChromaDB (P2-G)

`/logistics/documents` has a "🧠 Semantic" checkbox. When active, ChromaDB
cosine-similarity search is used instead of SQL LIKE matching. Falls back
gracefully to LIKE if the vector index is empty.

### ✅ Travel mode — full case management (P3-A through P3-E)

- **P3-A Expiry heatmap calendar** (`/travel/calendar`): 12-month grid heat-coded
  by expiry density, click to drill into any month's expiring documents.
  Expired documents surface in a separate red banner.
- **P3-B Family completeness gate**: per-member required-docs checklist
  (role-aware: head / spouse / child). Advance to IN_REVIEW is blocked until
  completeness = 100%.
- **P3-C Case status flow**: `COLLECTING → READY → IN_REVIEW → SUBMITTED →
  APPROVED / REJECTED` with specific blocker messages.
- **P3-D Family ZIP export**: one-click ZIP at `/travel/families/<id>/export`
  with source files organised by member, a CSV summary, and a family info file.
- **P3-E Next action + deadline**: free-text next-step field and date on family
  detail page, surfaced on family cards.

### ✅ Global hybrid search (P4-A)

`/search` accepts plain text, queries both logistics documents and travel
persons/families/documents by keyword + ChromaDB semantic similarity.
Integrated search input in the nav bar; keyboard shortcut `/` focuses it.

### ✅ Print-to-PDF (P4-B)

`@media print` CSS strips navigation, modals, and buttons; collapses glass
effects to white backgrounds; prevents page-break splits on tables and cards.
Every page has a "🖨 Print PDF" button.

### ✅ Keyboard shortcuts (P4-C)

`/` → focus global search, `?` → shortcuts overlay, `Esc` → close modal,
`D` → Dashboard, `U` → Upload, `A` → Analytics. Mode-aware: logistics adds
`R` (Review), `P` (Pipeline); travel adds `C` (Calendar), `F` (Families).

### ✅ Logistics analytics page (P5-A)

`/logistics/analytics` — Chart.js bar and line charts: container status mix,
top carriers by volume, shipments per month, average ETD→ETA transit time per
carrier. "🖨 Print PDF" button included.

### ✅ Travel analytics page (P5-B)

`/travel/analytics` — Chart.js doughnut and bar charts: case status
distribution, document type mix, top nationalities, 12-month expiry timeline.

### ✅ Low-confidence review queue

`/logistics/review` lists all documents below the 0.90 auto-approval threshold
that haven't been reviewed. Shows confidence score and which fields drove the
score down. "✓ Approve" button marks the document reviewed and logs it.

---

## Tech debt status (all P0–P3 items closed; P4–P12 closed except TD8 phase 2 + TD10)

| ID | Item | Status |
|----|------|--------|
| TD1 | Dual schema definitions (shipments DDL) | ✅ FIXED — `db.py` aligned with live schema |
| TD2 | Dead-code directories | ✅ DONE — `logistics_app/ ui/ database_logic/ utils/` deleted |
| TD3 | Migrations stub | ✅ DONE — 5 migrations, idempotent, runs on startup |
| TD4 | SQLite connection leak | ✅ FIXED — `processor.py` uses `try/finally` |
| TD5 | Fuzzy matching not wired | ✅ DONE — `projections.py` uses `resolve_identity` |
| TD6 | Tesseract subprocess per page | ✅ FIXED — module-level cache |
| TD7 | NL2SQL safety gate insufficient | ✅ HARDENED — forbidden keywords, no chaining, query_only |
| TD8 | server.py god file | 🟡 PHASE 1 DONE — 9 business modules extracted (-23% LOC, 3063→2368). Phase 2 (blueprints) deferred |
| TD9 | CORS wide open | ✅ FIXED — localhost-only, env-overridable |
| TD10 | No CSRF | ⚠️ DEFERRED until multi-user auth (L2) |
| TD11 | UI assets from CDN | ✅ DONE — tailwind/daisyui/htmx/chart.js vendored to `core/api/static/` |
| TD12 | Unstructured print() logging | ✅ DONE — `RotatingFileHandler` at `data/logs/bruns.log` |
| N5 | free_days from extracted docs | ✅ DONE — overrides carrier defaults via `free_days_from_documents` |

The historical detail of each item follows. They're kept for traceability —
the actual current state is in the table above.

### TD1 — Dual schema definitions (✅ fixed)

`core/storage/db.py::init_schema()` creates `shipments` with columns `carrier`,
`doc_type`, and `vessel`. The live `logistics.db` was created by
`database_logic/database.py` which uses `compagnie_maritime`, `document_type`,
and `vessel`. `server.py` and `projections.py` query the **legacy names**.

**Effect:** `init_schema()` from `core/storage/db.py` creates a DB that the
server cannot use. A fresh install on a new machine breaks immediately.

**Partial fix applied:** `documents` DDL in `core/storage/db.py` was updated to
include `reviewed_at TEXT` and `reviewed_by TEXT`. The `families` DDL was
updated to include `case_status`, `next_action`, `next_action_date`. The
`documents_travel` DDL was updated to include `original_doc_id`.

**Remaining:** The `shipments` table DDL in `core/storage/db.py` still uses
`carrier` / `doc_type` instead of `compagnie_maritime` / `document_type`. Fix:

```sql
CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tan TEXT UNIQUE,
    item_description TEXT,
    compagnie_maritime TEXT,
    port TEXT,
    transitaire TEXT,
    vessel TEXT,
    etd TEXT,
    eta TEXT,
    document_type TEXT,
    status TEXT,
    source_file TEXT,
    created_at TEXT,
    modified_at TEXT
)
```

**Effort:** 30 minutes.

### TD2 — Dead code directories still exist

`logistics_app/`, `database_logic/`, and `ui/` are the pre-migration Streamlit
era. They are not imported by any active `core/` code, but they sit in the
repo and confuse onboarding. `database_logic/database.py` is still the
authoritative schema source for the live DB, but only by accident — it should
be replaced by the fixed `core/storage/db.py`.

**Fix:** Delete `logistics_app/`, `ui/`, and migrate `database_logic/` schema
into `core/storage/db.py` as part of TD1 completion. Then delete `database_logic/`.

**Effort:** 1 hour after TD1 is fixed.

### TD3 — `migrations.py` body is empty (CRITICAL for schema changes)

`core/storage/migrations.py::run_migrations()` contains only comments and `pass`.
All schema changes since launch have been applied with manual ALTER TABLE
statements run once. The next schema change will require manual intervention
on every deployed instance.

**Fix:** Implement the migration runner with a `schema_version` table:
```python
MIGRATIONS = [
    ("001_add_reviewed_fields",
     "ALTER TABLE documents ADD COLUMN IF NOT EXISTS reviewed_at TEXT; "
     "ALTER TABLE documents ADD COLUMN IF NOT EXISTS reviewed_by TEXT"),
    ("002_add_families_case_fields",
     "ALTER TABLE families ADD COLUMN IF NOT EXISTS case_status TEXT DEFAULT 'COLLECTING'; "
     "ALTER TABLE families ADD COLUMN IF NOT EXISTS next_action TEXT; "
     "ALTER TABLE families ADD COLUMN IF NOT EXISTS next_action_date TEXT"),
    ("003_add_original_doc_id",
     "ALTER TABLE documents_travel ADD COLUMN IF NOT EXISTS original_doc_id INTEGER"),
    ("004_add_indexes",
     "CREATE INDEX IF NOT EXISTS idx_docs_module ON documents(module); "
     "CREATE INDEX IF NOT EXISTS idx_shipments_eta ON shipments(eta); "
     "CREATE INDEX IF NOT EXISTS idx_containers_status ON containers(statut_container)"),
]
```

**Effort:** Half a day.

### TD4 — SQLite connection leaked on pipeline exception (CRITICAL)

In `core/pipeline/processor.py::process_file()`, `conn.close()` is inside the
`try` block at line 134. If any exception fires before that line, the connection
is never closed. Under load (many failing documents), this exhausts file handles.

**Fix:** Move to `finally`:
```python
conn = None
try:
    conn = get_connection(self.db_path)
    # ... processing ...
    conn.close()
except Exception as e:
    job.fail(str(e))
finally:
    if conn:
        try: conn.close()
        except: pass
```

**Effort:** 10 minutes.

### TD5 — Fuzzy identity matching engine built but not wired

`core/matching/engine.py` is complete and correct (RapidFuzz, weighted
name/DOB/nationality scoring, AUTO_MERGE/REVIEW/NEW_IDENTITY thresholds).
`projections.py` uses `WHERE normalized_name = ?` instead. Name variations
create duplicate person rows silently.

This is the Travel module's core differentiator. It is complete code sitting
unused. See `docs/02-HOW-IT-WORKS.md` Step 6 for the wiring recipe.

**Effort:** 1 day.

### TD6 — `_tesseract_available()` spawns subprocess per page

For a 20-page scanned PDF, 20 `tesseract --version` subprocesses are spawned.
No module-level cache exists. The `_TESSERACT_AVAILABLE` pattern shown in
previous planning was never applied.

**Fix:**
```python
_TESSERACT_AVAILABLE: bool | None = None

def _tesseract_available() -> bool:
    global _TESSERACT_AVAILABLE
    if _TESSERACT_AVAILABLE is None:
        try:
            import pytesseract; pytesseract.get_tesseract_version()
            _TESSERACT_AVAILABLE = True
        except Exception:
            _TESSERACT_AVAILABLE = False
    return _TESSERACT_AVAILABLE
```

**Effort:** 10 minutes.

### TD7 — NL2SQL safety gate is insufficient

The check `if not sql_upper.startswith("SELECT")` is bypassable:
`SELECT 1; DROP TABLE containers;` passes. No forbidden keyword list, no
`PRAGMA query_only` connection constraint.

**Fix:**
```python
FORBIDDEN = ("INSERT","UPDATE","DELETE","DROP","CREATE","ALTER",
             "ATTACH","DETACH","PRAGMA","VACUUM","TRUNCATE")
if any(kw in sql.upper().split() for kw in FORBIDDEN):
    return error_fragment("Forbidden keyword in generated SQL")
conn = get_connection(LOGISTICS_DB)
conn.execute("PRAGMA query_only = ON")
cursor = conn.execute(sql)
```

**Effort:** 2 hours.

### TD8 — `server.py` is ~3 000 LOC god file

**Current size: 3 063 LOC** (was ~1 700 in previous docs — grew by 1 300+ lines
across the improvement sessions). All Flask routes, business logic (demurrage
calculation, family completeness, NL2SQL, reconciliation, chart data),
file serving, and inline SQL live in one file.

**Fix:** Decompose into Flask blueprints:
```
core/api/blueprints/
    logistics.py    # /logistics/* routes
    travel.py       # /travel/* routes
    api.py          # /api/* JSON endpoints
    llm.py          # /llm/* config
    files.py        # /files/* serving + annotation
    search.py       # /search
```
Move all SQL into `core/storage/repository.py`. Move business logic into
`core/` submodules.

**Effort:** 3–4 days.

### TD9 — CORS is wide open

`CORS(app, resources={r"/api/*": {"origins": "*"}})` — any web page can read
the logistics database over the LAN.

**Fix:**
```python
CORS(app, resources={r"/api/*": {"origins": ["http://localhost:*", "http://127.0.0.1:*"]}})
```

**Effort:** 5 minutes.

### TD10 — No CSRF on state-changing endpoints

HTMX POST endpoints (upload, edit, family status update, clear input, purge
jobs) have no CSRF protection. A malicious page in the same browser can POST
to them cross-origin.

**Fix:** Add `flask-wtf` and inject CSRF token via `hx-headers`.

**Effort:** Half a day.

### TD11 — Tailwind and DaisyUI loaded from CDN

The app markets itself as offline-capable but loads ~300 KB of CSS/JS from
external CDNs on every page load. Port offices with no internet get blank UI.

**Fix:** Download and serve from `core/api/static/`:
```bash
curl https://cdn.jsdelivr.net/npm/daisyui@4.12.10/dist/full.min.css \
  -o core/api/static/daisyui.min.css
curl https://unpkg.com/htmx.org@2.0.3 -o core/api/static/htmx.min.js
curl https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js \
  -o core/api/static/chart.min.js
# Tailwind: use the standalone CLI or pre-built CDN build
```
Update `base.html` + analytics templates to reference `{{ url_for('static', ...) }}`.

**Effort:** 1 hour.

### TD12 — No structured logging

All application logging uses `print()`. No log levels, no file output, no
correlation with job IDs or request IDs.

**Fix:** Replace `print()` with `logging.getLogger(__name__)` calls throughout
`core/`. Configure a `RotatingFileHandler` writing to `data/logs/bruns.log`.

**Effort:** Half a day.

---

## Next features (June–August 2026)

### N1 — Wire fuzzy identity matching (Travel) ⭐ most urgent

Described in TD5. The matching engine is complete. Every duplicate person
row is a real business failure. This is the #1 feature gap.

**Effort:** 1 day.

### N2 — Implement schema migrations

Described in TD3. Without this, any schema change on a deployed instance
requires manual SQL. Unacceptable for a product being sold to operators.

**Effort:** Half a day.

### N3 — Apache Parquet export endpoint ✅ DONE

`/api/logistics/shipments_full.parquet` is live — returns the full flat
shipments+containers view as a single Apache Parquet file (~10× faster ingest
into Power BI than JSON pagination). The column projection is shared with the
JSON endpoint via `_SHIPMENTS_FULL_SELECT`, so the two can never drift; the
same `status` / `carrier` / `tan` filters apply. `pyarrow` is now an explicit
runtime dependency. Empty result sets still carry the full column schema (built
from the cursor description). If `pyarrow` is missing the endpoint returns a
clear `503` instead of a 500 traceback.

### N4 — Pre-computed analytics API

`/api/analytics/kpis?period=90d` calculating standard logistics KPIs
server-side. Reduces Power BI DAX formula requirement to near-zero.

**Effort:** 2 days.

### N5 — Demurrage free days from extracted documents

The LLM prompt already extracts `free_days` and `demurrage_terms` from
booking confirmations. `_demurrage_info()` ignores them and uses hardcoded
carrier defaults. Wire them:
```python
doc_free_days = json.loads(doc_row["extracted_json"] or "{}").get("free_days")
free_days = int(doc_free_days or 0) or _DEFAULT_FREE_DAYS.get(carrier, 14)
```

**Effort:** 2 hours.

### N6 — Arrival Notice document type

Add `ARRIVAL_NOTICE` to logistics prompts. Extract `date_avis_arrivee` and
use it as the demurrage clock start instead of `eta`. This makes all demurrage
calculations accurate (currently off by the port processing lag).

**Effort:** 1 day.

### N7 — Exception / alert dashboard widget

Surface approaching demurrage (< 3 days free), missing BOL, customs overdue,
and containers without a delivery date as a dedicated view. All data already
exists — only the UI surface is missing.

**Effort:** 2 days.

### N8 — Cloud LLM fallback (premium tier)

Add `OpenAIClient` and `AnthropicClient` subclasses of `LLMClient`. On local
Ollama failure or confidence < 0.60, escalate to cloud. Audit log records
which provider answered.

**Effort:** 2 days.

---

## Later (Sep–Dec 2026)

### L2 — Multi-user auth

Basic auth at Flask layer. `reviewed_at` / `modified_by` columns finally
fillable by named users. Role split: `viewer` / `operator` / `admin`.

### L3 — OData v4 endpoint

Power BI and Excel have first-class OData connectors (automatic pagination,
filtering, schema discovery).

### L4 — Healthcare domain plug-in

`modules/healthcare/` as a forcing function for the "1 day per new domain"
target.

---

## Bugs fixed during development sessions

These were live bugs caught during development and patched immediately:

| Bug | File | Fix |
|-----|------|-----|
| `disc.values` resolved to Python's built-in `dict.values()` method in Jinja2, causing 500 on any document with TAN siblings | `document_detail.html:222` | Changed to `disc['values'].items()` |
| `reviewed_at` / `reviewed_by` columns queried but not in schema DDL | `core/storage/db.py` | Added to `documents` CREATE TABLE |
| `families` table missing `case_status`, `next_action`, `next_action_date` in DDL | `core/storage/db.py` | Added to `families` CREATE TABLE |
| `documents_travel` missing `original_doc_id` in DDL | `core/storage/db.py` | Added to `documents_travel` CREATE TABLE |
| Chart.js infinite growth loop on analytics pages | `logistics/analytics.html`, `travel/analytics.html` | Wrapped each `<canvas>` in `<div class="relative h-64">` to give Chart.js a fixed-height parent |
| Multiple stale Flask processes accumulating on port 7845 (Windows) | `START_APP.bat` | Kill existing process before starting: `for /f "tokens=5" %a in ('netstat -aon ^| findstr :7845') do taskkill /F /PID %a` |

---

## Insights that drive product direction

### Insight 1 — The Excel format IS the API

The 49-column "Containers actifs" XLSX is the integration contract with the
customer's Power BI. Schema changes that break the XLSX shape must be versioned.
New computed columns live in the export layer, not the database.

### Insight 2 — Cache hit rate is the real operational KPI

A high cache hit rate means the operator is re-uploading the same files —
which is fine and instant. Expose this in the dashboard settings page.

### Insight 3 — Fuzzy identity resolution is the Travel module's moat

OCR + LLM is commoditized. What is not commoditized is catching "Mohammed
AbdelKader b. 1985-03-12" = "Mohammed Abdul-Khader" on case #4521 from 2 years
ago. The matching engine is complete. Wire it.

### Insight 4 — Local-first is a 5-year posture

Data sovereignty laws (Algeria's Loi 18-07, Morocco, Tunisia) make cloud-first
SaaS structurally disadvantaged in the primary target market. Don't dilute it.

### Insight 5 — The competitor is the clerk, not other software

The pitch is "stop paying €600/mo for typing." Not "better than Nanonets."

### Insight 6 — French language is a moat

Every prompt, UI string, and export column is in French. US/UK competitors need
a localization pass. The LLM handles French + technical terms (CNE, PON, MARKS
AND NUMBERS) gracefully because the prompt teaches it.

---

## Things explicitly NOT on the roadmap

| Idea | Why not |
|------|---------|
| Mobile app | The buyer uses a Windows laptop |
| Multi-tenant SaaS | Conflicts with local-first positioning |
| Email auto-ingestion | Security blast radius; defer indefinitely |
| Generic LLM chatbot | Not the product |
| Custom LLM training | Llama3 + good prompts is sufficient |
