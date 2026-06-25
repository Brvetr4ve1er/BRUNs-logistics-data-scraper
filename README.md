# BRUNs Document Intelligence Platform

A modular, production-grade Document Intelligence System designed to process, extract, normalize, match, and manage structured data from real-world documents (PDFs, Images, Text). 

Built with a "Core-First" architecture, the platform securely isolates multiple domain-specific applications (Logistics and Travel) while sharing a unified, high-performance extraction engine backed by local LLMs (Ollama) and specialized OCR pipelines.

---

## 🏗️ System Architecture

The platform operates on a strict **Modular Architecture** to guarantee data isolation and system stability.

```text
BRUNs logistics data scraper/
├── core/                      ← Engine (stateless, reusable, UI-agnostic)
│   ├── api/                   ← Flask + Jinja templates + HTMX (the UI)
│   │   ├── server.py          ← All HTML routes + JSON BI endpoints (port 7845)
│   │   ├── llm_config.py      ← Provider config (Ollama / LM Studio / OpenAI / Anthropic)
│   │   ├── job_tracker.py     ← In-memory job dict for HTMX progress polling
│   │   └── templates/         ← DaisyUI synthwave (31-theme picker, no build step)
│   │       ├── base.html      ← Shared layout, theme picker, LLM modal shell
│   │       ├── mode_picker.html
│   │       ├── logistics/     ← Dashboard, upload, edit, export, settings
│   │       └── travel/        ← Dashboard, upload, persons, families, documents
│   ├── extraction/            ← LLM integration, OCR (Tesseract), chunking
│   ├── matching/              ← Fuzzy-matching identity resolution
│   ├── normalization/         ← Names / dates / codes canonicalization
│   ├── pipeline/              ← Job orchestration, caching, routing
│   ├── search/                ← ChromaDB semantic search
│   ├── storage/               ← SQLite repository, paginator, exporters
│   ├── schemas/               ← Pydantic models per domain
│   ├── validation/            ← Post-extraction validators
│   └── audit/                 ← Structured logging
├── modules/                   ← Domain-specific config + prompts
│   ├── logistics/             ← Shipping / containers / TAN
│   └── travel/                ← Passports / families / IDs (with MRZ parser)
├── data/                      ← Databases + uploads + logs + exports
│   ├── logistics.db           ← Logistics SQLite
│   ├── travel.db              ← Travel SQLite
│   ├── input/                 ← Uploaded PDFs
│   ├── exports/               ← Generated CSV / XLSX exports
│   ├── logs/                  ← Per-job extraction logs
│   ├── trial_files/           ← Sample PDFs for testing
│   ├── .llm_config.json       ← Persisted LLM provider config
│   └── .launcher_prefs.json   ← UI preferences
├── tesseract_bin/             ← Bundled Tesseract OCR (no separate install needed)
├── tessdata/                  ← Tesseract language models
├── tests/                     ← Pytest suite + import-rule enforcement
├── docs/                      ← Project wiki (Markdown)
├── START_APP.bat              ← Launches Flask UI + opens browser → http://localhost:7845
├── START_BI_CONNECTOR.bat     ← Launches Flask headless (Power BI mode)
└── INSTALL.bat                ← First-run venv + dependency setup
```

**Single-process architecture.** One Flask server (port 7845) serves both:
- **HTML UI** (DaisyUI synthwave + HTMX, no build step) at `/`, `/logistics/...`, `/travel/...`
- **JSON BI endpoints** at `/api/...` for Power BI / Tableau / Excel.

---

## ✨ Core Features & Capabilities

### 1. Deterministic Data Extraction & Validation
*   **Dual-Pipeline Engine:** Combines deterministic text parsing (e.g., MRZ codes from passports) with probabilistic LLM extraction for unstructured documents (e.g., invoices).
*   **Strict Pydantic Validation:** Every extracted data point is validated against strict Python type schemas before it is allowed to touch the database.

### 2. Enterprise Scale Performance
*   **Cryptographic Extraction Cache:** If the exact same document is uploaded twice, the system calculates its `sha256` hash and instantly pulls the result from the cache, bypassing the LLM entirely.
*   **Background Queues:** Powered by `Huey` (SQLite-backed), allowing operators to upload 100+ documents at once without freezing the UI.
*   **Semantic Vector Search:** Integrates `ChromaDB` to embed all documents, allowing users to search thousands of documents using natural language queries.

### 3. Identity Resolution (Travel Module)
*   **Fuzzy Matching Engine:** Automatically calculates similarity scores between names, birth dates, and nationalities using `rapidfuzz`. 
*   **Review Thresholds:** Duplicates with a score > `0.85` are auto-merged, while scores > `0.60` are flagged for human review in the UI.

### 4. Visual Verification
*   **Bounding Box Highlighting:** Extracts text blocks using `PyMuPDF` and renders yellow highlights directly on the PDF inside the dashboard, dropping manual verification time to seconds.

---

## 🚀 Running the Application

### First-time install (clean machine)

**Windows:**
```cmd
git clone https://github.com/Brvetr4ve1er/BRUNs-logistics-data-scraper.git
cd BRUNs-logistics-data-scraper
INSTALL.bat
START_APP.bat
```

**Linux / macOS:**
```bash
git clone https://github.com/Brvetr4ve1er/BRUNs-logistics-data-scraper.git
cd BRUNs-logistics-data-scraper
chmod +x install.sh start.sh
./install.sh
./start.sh
```

`INSTALL.bat` / `install.sh` is idempotent — it walks 7 steps (Python check
→ venv → pip install → Tesseract + language packs → Ollama probe →
data dirs → DB migrations) and skips anything already done. Re-running is
safe.

After install, just use `START_APP.bat` (or `./start.sh`) — it bootstraps
the venv automatically if anything is missing and opens the browser at
`http://localhost:7845/`.

### Prerequisites the installer expects

| Requirement | Auto-installed? | Manual fallback |
|---|---|---|
| Python 3.10+ | No (must be on PATH) | https://www.python.org/downloads/ |
| Tesseract OCR | Yes (Windows: `install_tesseract.bat`) | macOS: `brew install tesseract` · Linux: `apt install tesseract-ocr` |
| Tesseract language packs (eng/fra/ara) | Yes (downloaded into `tessdata/`) | https://github.com/tesseract-ocr/tessdata |
| Ollama + llama3 model | Probed; offers to `ollama pull llama3` | https://ollama.com/download |

For full troubleshooting see **[INSTALLATION.md](INSTALLATION.md)**.

---

## 📚 Documentation

- **[INSTALLATION.md](INSTALLATION.md)** — install on a clean machine + troubleshooting
- Full project wiki: [`docs/`](docs/README.md)

Highlights from the wiki:

- [Overview](docs/01-OVERVIEW.md) — what this is, who it's for, why local-first
- [How it works](docs/02-HOW-IT-WORKS.md) — end-to-end pipeline walkthrough
- [Technology stack](docs/03-TECHNOLOGY-STACK.md) — every dep and why
- [Domain modules](docs/04-DOMAIN-MODULES.md) — Logistics + Travel deep dive
- [Data model](docs/05-DATA-MODEL.md) — schemas, tables, the 49-column export
- [API reference](docs/06-API-REFERENCE.md) — Flask BI endpoints
- [Use cases](docs/07-USE-CASES.md) — where this fits in the real world
- [Marketing plan](docs/08-MARKETING-PLAN.md) — positioning, ICP, GTM
- [Roadmap & improvements](docs/09-ROADMAP-IMPROVEMENTS.md) — what's next
- [Tech debt cleanup plan](docs/TECH-DEBT-CLEANUP.md) — surgical strategy
- [Architecture rules](docs/ARCHITECTURE.md) — package boundaries (enforced by tests)

---

## 🔌 BI Compatibility (already shipped)

The platform exposes the live SQLite databases as paginated REST endpoints
through a local Flask server (`core/api/server.py`, default port `7845`).
Power BI, Tableau, Looker, or any HTTP client can connect directly — no
manual Excel exports required.

Start the BI bridge:
```powershell
.\START_BI_CONNECTOR.bat
```

Primary Power BI endpoint (one row per container, all 49+ columns flat):
```
http://localhost:7845/api/logistics/shipments_full
```

For large datasets, the same view is available as a single Apache Parquet file —
~10× faster ingest into Power BI / pandas than paging through JSON:
```
http://localhost:7845/api/logistics/shipments_full.parquet
```

Full endpoint catalog and Power BI connection recipe: [API reference](docs/06-API-REFERENCE.md).

---

## 🎯 Future Roadmap

The detailed roadmap (with tech-debt items and architectural insights) lives
in [docs/09-ROADMAP-IMPROVEMENTS.md](docs/09-ROADMAP-IMPROVEMENTS.md). At a
glance:

### Done
- Flask + HTMX + DaisyUI synthwave UI (single-process app on port 7845)
- Logistics: Dashboard / Upload / Edit / Export / Settings
- Travel: Dashboard / Upload / Persons / Families / Documents
- LLM config modal (4 providers, live model discovery, persisted)
- 31-theme picker (synthwave default, persisted via localStorage)
- Apache Parquet BI export (`/api/logistics/shipments_full.parquet`)
- This wiki

### Now (in progress)

### Next (Q3 2026)
- Job tracker persistence (resumable uploads after restart)
- Computed BI columns moved into a SQL VIEW (cleaner architecture)
- Demurrage risk computation endpoint (`/api/logistics/risk/demurrage`)
- Optional cloud LLM fallback (premium accuracy tier)

### Later (Q4 2026)
- Human-in-the-loop review UI for fuzzy-match conflicts
- Bounding-box overlay on the source PDF (verification time → seconds)
- OData v4 endpoint (native Power BI / Excel connector)
- Healthcare domain plug-in (proof of "1 day per new domain" target)

### Explicitly NOT on the roadmap
Mobile app, multi-tenant SaaS, email auto-ingestion, custom LLM training.
See the roadmap doc for reasoning.
