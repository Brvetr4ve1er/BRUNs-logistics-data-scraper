# 06 — API Reference

The Flask app in `core/api/server.py` serves both the HTML operator UI and
paginated REST endpoints. Power BI, Tableau, Looker, Excel "Get Data > Web",
or any HTTP client can consume the JSON endpoints.

## Server basics

| | |
|---|---|
| Default host | `0.0.0.0` (binds all interfaces) |
| Default port | `7845` |
| Start | `python -m core.api.server` or `START_APP.bat` |
| Data dir | `data/` (override with `BRUNS_DATA_DIR`) |
| CORS | Enabled on `/api/*`, currently `origins="*"` (all origins) |
| Encoding | UTF-8 native (`JSON_AS_ASCII = False` — French chars survive) |
| Pagination cap | `page_size` hard-capped at 200 |

## Common query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int ≥ 1 | 1 | Page number |
| `page_size` | int 1–200 | 50 | Rows per page |

All paginated endpoints return:

```json
{
  "data": [...],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total": 1234,
    "total_pages": 25,
    "has_next": true,
    "has_prev": false
  }
}
```

---

## System endpoints

### `GET /api/status`

Health check. Returns whether each domain database file exists.

**Response:**
```json
{
  "status": "online",
  "databases": {
    "logistics": "ok",
    "travel": "missing"
  }
}
```

Use this in BI tools as the connection-test endpoint.

---

## Logistics endpoints

### `GET /api/logistics/shipments`

Shipment-level rows from the `shipments` table.

**Filters:**
- `status` — exact match on `shipments.status` (`BOOKED`, `IN_TRANSIT`, `UNKNOWN`)

**Note on column names:** The response uses the live DB column names:
`compagnie_maritime` (not `shipping_company`), `vessel` (not `vessel_name`),
`tan` (not `tan_number`).

**Example:**
```
GET /api/logistics/shipments?status=IN_TRANSIT&page=1&page_size=100
```

**Response (truncated):**
```json
{
  "data": [
    {
      "id": 234,
      "tan": "TAN/1234/2026",
      "vessel": "MSC LAUREN",
      "compagnie_maritime": "MSC",
      "etd": "2026-03-11",
      "eta": "2026-03-25",
      "status": "IN_TRANSIT",
      "document_type": "DEPARTURE",
      "transitaire": "CEVA",
      "port": "Port d'Alger",
      "item_description": "Canette Al HEINEKEN 330ml",
      "source_file": "C:\\Users\\ROG STRIX\\...\\booking.pdf"
    }
  ],
  "pagination": {...}
}
```

> **Security note:** `source_file` returns the full absolute Windows path of the
> original uploaded file. This exposes the local filesystem structure to any
> API consumer. Consider filtering this out in BI tool Power Query, or raise
> with the roadmap to strip it at the API layer.

### `GET /api/logistics/containers`

Container-level rows from the `containers` table.

**Filters:**
- `status` — exact match on `containers.statut_container`

**Example:**
```
GET /api/logistics/containers?status=IN_TRANSIT
```

### `GET /api/logistics/shipments_full` ⭐ Primary BI endpoint

Flat denormalized view. **One row per container**, all columns from
`shipments` JOIN `containers`. This is the primary endpoint for Power BI —
no joins required on the BI side.

All column names match the French "Containers actifs" Excel format.

**Filters:**
- `status` — `shipments.status`
- `carrier` — `shipments.compagnie_maritime` (exact match)
- `tan` — substring match on `shipments.tan` (LIKE `%value%`)

**Example:**
```
GET /api/logistics/shipments_full?carrier=MSC&status=IN_TRANSIT
```

**Response sample (key fields):**
```json
{
  "data": [
    {
      "shipment_id": 234,
      "container_id": 891,
      "N° Container": "MSCU1234567",
      "N° TAN": "TAN/1234/2026",
      "Item": "Canette Al HEINEKEN 330ml",
      "Compagnie maritime": "MSC",
      "Port": "Port d'Alger",
      "Transitaire": "CEVA",
      "Navire": "MSC LAUREN",
      "Date shipment": "2026-03-11",
      "Date accostage": "2026-03-25",
      "Statut Expédition": "IN_TRANSIT",
      "Type document": "DEPARTURE",
      "Statut Container": "IN_TRANSIT",
      "Container size": "40 feet",
      "N° Seal": "SEAL12345",
      "Date livraison": null,
      "Site livraison": null,
      "Date dépotement": null,
      "Date début Surestarie": null,
      "Nbr jours surestarie estimés": 0,
      "Date réstitution": null,
      "Montant facturé (DA)": 0.0,
      "Taux de change": 0.0,
      "Source": "C:\\Users\\ROG STRIX\\...\\booking.pdf",
      "Créé le": "2026-03-12T10:23:45",
      "Modifié le": "2026-03-12T10:23:45"
    }
  ],
  "pagination": {...}
}
```

---

### `GET /api/logistics/shipments_full.parquet` ⭐ Bulk BI export

Same flat view and columns as `shipments_full`, but returned as a single
**Apache Parquet** file instead of paginated JSON. Power BI and pandas ingest
this ~10× faster than paging through JSON, and the binary columnar encoding is
a fraction of the size on the wire.

Unlike the JSON endpoint this is **not paginated** — the entire filtered result
set comes back in one file.

**Filters:** identical to `shipments_full` — `status`, `carrier`, `tan`.

**Response:** `Content-Type: application/vnd.apache.parquet`, served as an
attachment named `shipments_full.parquet`. Columns match the JSON endpoint
exactly (the column projection is shared in code, so the two can never drift).
An empty result still carries the full column schema.

**Example (Power BI / pandas):**
```python
import pandas as pd
df = pd.read_parquet("http://localhost:7845/api/logistics/shipments_full.parquet?carrier=MSC")
```

In Power BI Desktop: **Get Data → Web → URL** → paste the endpoint. Power BI
detects the Parquet format automatically.

> Requires `pyarrow` (installed via `requirements.txt`). If it is missing the
> endpoint returns `503` with a clear message rather than a 500 traceback.

---

## Travel endpoints

### `GET /api/travel/persons`

**Filters:**
- `nationality` — exact match (3-letter ISO preferred)

**Example:**
```
GET /api/travel/persons?nationality=DZA
```

### `GET /api/travel/families`

No filters (full list).

### `GET /api/travel/documents`

**Filters:**
- `doc_type` — exact match on `PASSPORT`, `ID_CARD`, etc.

---

## HTML UI routes

These serve the operator interface — not intended for BI tool consumption.

| Route | Description |
|-------|-------------|
| `GET /` | Mode picker (splash page) |
| `GET /logistics` | Logistics dashboard |
| `GET /logistics/upload` | Upload form |
| `POST /logistics/upload` | Submit files; returns HTMX polling fragment |
| `GET /logistics/process-status/<job_id>` | Job status polling (HTMX target) |
| `GET /logistics/documents` | Document list |
| `GET /logistics/documents/<id>` | Document detail + PDF split view |
| `POST /logistics/documents/<id>` | Inline edit extracted fields |
| `GET /logistics/containers/<id>` | Container detail + edit form |
| `POST /logistics/containers/<id>` | Save operational fields |
| `POST /logistics/ask` | NL2SQL natural-language query |
| `GET /logistics/sheet` | Spreadsheet-style container view |
| `GET /logistics/swimlane` | Pipeline swimlane view |
| `GET /logistics/review` | Low-confidence review queue |
| `GET /logistics/analytics` | Analytics charts |
| `GET /logistics/export` | CSV/XLSX export |
| `GET /logistics/settings` | Settings page |
| `GET /travel` | Travel dashboard |
| `GET /travel/upload` | Travel upload form |
| `POST /travel/upload` | Submit travel files |
| `GET /travel/persons` | Persons list (with search) |
| `GET /travel/persons/<id>` | Person detail |
| `POST /travel/persons/<id>` | Update person fields |
| `GET /travel/families` | Families list |
| `GET /travel/families/<id>` | Family detail + completeness gate |
| `POST /travel/families/<id>` | Update case status / next action |
| `GET /travel/families/<id>/export` | Download family ZIP dossier |
| `GET /travel/documents` | Travel documents list |
| `GET /travel/documents/<id>` | Travel document detail |
| `POST /travel/documents/<id>` | Update doc fields |
| `GET /travel/calendar` | Expiry heatmap (next 12 months) |
| `GET /travel/analytics` | Travel analytics |
| `GET /files/<module>/<doc_id>` | Serve original file (PDF/image) |
| `GET /files/<module>/<doc_id>/annotated` | Annotated PDF page (PNG, with extraction highlights) |
| `GET /search` | Global semantic search (ChromaDB) |
| `GET /llm/config` | LLM configuration modal body (HTMX) |
| `POST /llm/test` | Test LLM connection |
| `POST /llm/save` | Save LLM config to `data/.llm_config.json` |

---

## Power BI connection recipe

1. Open Power BI Desktop → **Get Data > Web**.
2. URL: `http://localhost:7845/api/logistics/shipments_full?page_size=200`
3. Authentication: **Anonymous**.
4. In Power Query: expand the `data` list, then expand each record.
5. For datasets > 200 rows: parameterize `page` and page through until
   `pagination.has_next == false`.

For very large datasets (>10k rows): use the XLSX export instead — the BI
endpoint is designed for live freshness, not bulk extract.

---

## Authentication and security

**Currently:** None. The server binds to `0.0.0.0` and CORS is `origins="*"`.

**Acceptable because:** The intended deployment is a single laptop, single
operator, private LAN.

**To restrict to localhost only**, the last line of `core/api/server.py`:
```python
# Change from:
app.run(host="0.0.0.0", port=port, debug=False)
# To:
app.run(host="127.0.0.1", port=port, debug=False)
```

Also restrict CORS:
```python
CORS(app, resources={r"/api/*": {"origins": ["http://localhost:*", "http://127.0.0.1:*"]}})
```

**Known gaps:** No CSRF protection on state-changing HTMX endpoints. No auth
on upload/edit/delete routes. These are tracked in
[09-ROADMAP-IMPROVEMENTS.md](09-ROADMAP-IMPROVEMENTS.md).

---

## Planned future endpoints

| Endpoint | Purpose | Status |
|----------|---------|--------|
| `GET /api/logistics/shipments_full.parquet` | Bulk export in Parquet (10× faster for Power BI) | ✅ Live (N3) |
| `GET /api/analytics/kpis` | Pre-computed logistics KPIs (avg demurrage, transit time, etc.) | Planned (N4) |
| `GET /api/analytics/carrier-performance` | Per-carrier performance metrics | Planned (N4) |
| `GET /api/logistics/odata/$metadata` | OData v4 service document | Planned (L3) |
| `GET /api/logistics/risk/demurrage` | Live demurrage exposure per active container | Planned (N7) |

## HTML UI routes added since initial documentation

These routes are live but were added after `06-API-REFERENCE.md` was originally written:

| Route | Description |
|-------|-------------|
| `GET /logistics/review` | Low-confidence document review queue |
| `POST /logistics/review/<id>/approve` | Approve a document from the queue |
| `POST /logistics/documents/<id>/reextract` | Re-run extraction, return diff modal |
| `POST /logistics/documents/<id>/reextract/accept` | Accept re-extraction result |
| `GET /logistics/analytics` | Logistics analytics dashboard (Chart.js) |
| `GET /travel/families/<id>` | Family detail with completeness gate + case flow |
| `POST /travel/families/<id>` | Update case status, next action, deadline |
| `GET /travel/families/<id>/export` | Download family dossier ZIP |
| `GET /travel/calendar` | Document expiry heatmap (next 12 months) |
| `GET /travel/analytics` | Travel analytics dashboard (Chart.js) |
| `GET /search` | Global hybrid search (keyword + ChromaDB semantic) |
| `GET /files/<module>/<doc_id>/annotated` | Annotated page PNG with extraction highlights |
