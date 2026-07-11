"""Natural-language → SQL ("Ask your data") engine.

Two responsibilities, both pure:

1. `generate_sql_from_question(question)` — call the configured LLM with a
   compact schema description and return the raw SQL string. Handles markdown
   fence stripping and gracefully falls back to localhost Ollama defaults if
   `data/.llm_config.json` is missing.

2. `validate_select(sql)` — three-tier safety gate (TD7):
   - Must start with SELECT or WITH (CTE form)
   - Must NOT contain `;` (statement chaining)
   - Must NOT contain any data-modification or schema keyword as a token

The Flask route in server.py adds defense in depth by setting
`PRAGMA query_only = ON` on the execution connection — even if `validate_select`
ever has a false negative, the database can't be modified.

Public API:
    NL2SQL_SCHEMA, NL2SQL_SYSTEM       (prompt strings)
    FORBIDDEN_SQL_KEYWORDS             (frozenset[str])
    generate_sql_from_question(q) -> str
    validate_select(sql) -> tuple[bool, str | None, str]
        Returns (ok, error_message, cleaned_sql).
"""
from __future__ import annotations

import re

# ─── Compact schema description fed to the LLM ──────────────────────────────
NL2SQL_SCHEMA = """
Tables in the logistics SQLite database:

shipments: id, tan TEXT, item_description TEXT, compagnie_maritime TEXT,
  port TEXT, transitaire TEXT, vessel TEXT, etd TEXT (YYYY-MM-DD),
  eta TEXT (YYYY-MM-DD), document_type TEXT, status TEXT, source_file TEXT,
  created_at TEXT

containers: id, shipment_id INTEGER (FK→shipments.id),
  container_number TEXT, size TEXT, seal_number TEXT,
  statut_container TEXT (BOOKED|IN_TRANSIT|ARRIVED|DELIVERED|RESTITUTED),
  date_livraison TEXT (YYYY-MM-DD), site_livraison TEXT,
  date_depotement TEXT, date_debut_surestarie TEXT,
  date_restitution_estimative TEXT, nbr_jours_surestarie_estimes INTEGER,
  nbr_jours_perdu_douane INTEGER, date_restitution TEXT,
  restitue_camion TEXT, restitue_chauffeur TEXT, centre_restitution TEXT,
  livre_camion TEXT, livre_chauffeur TEXT, montant_facture_da REAL,
  taux_de_change REAL, n_facture_cm TEXT, commentaire TEXT,
  date_declaration_douane TEXT, date_liberation_douane TEXT,
  created_at TEXT, modified_at TEXT

To query across both, use JOIN: containers c JOIN shipments s ON s.id = c.shipment_id
""".strip()


NL2SQL_SYSTEM = f"""You are a SQLite SQL generator for a logistics database.
When given a question in French or English, return ONLY a valid SQLite SELECT query.
No explanation, no markdown, no code fences — just the SQL.

{NL2SQL_SCHEMA}

Rules:
- Always use SELECT. Never INSERT, UPDATE, DELETE, DROP, or PRAGMA.
- Use LIKE for fuzzy string matching (e.g. compagnie_maritime LIKE '%CMA%')
- For "today" use date('now'), for date comparisons use ISO strings
- LIMIT 50 unless the question asks for all
- If unsure, return: SELECT 'Unable to generate SQL for this question' AS message
"""


# ─── Safety gate ────────────────────────────────────────────────────────────

FORBIDDEN_SQL_KEYWORDS: frozenset[str] = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "TRUNCATE",
    "REPLACE", "UPSERT", "MERGE", "REINDEX", "ANALYZE",
})

# SQLite functions that can touch the filesystem, load native code, or otherwise
# escape a read-only SELECT. These are valid tokens inside an otherwise-innocent
# SELECT, so keyword-prefix checks miss them — block them by name.
FORBIDDEN_SQL_FUNCTIONS: frozenset[str] = frozenset({
    "LOAD_EXTENSION", "READFILE", "WRITEFILE", "EDITDIST3",
    "FTS3_TOKENIZER", "ZIPFILE", "SQLITE_COMPILEOPTION_USED",
    "SQLITE_COMPILEOPTION_GET",
})

_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def validate_select(sql: str) -> tuple[bool, str | None, str]:
    """Three-tier safety gate. Returns (ok, error_message, cleaned_sql).

    `cleaned_sql` is the input with leading/trailing whitespace and a single
    trailing semicolon stripped — safe to pass to conn.execute().
    """
    cleaned = sql.strip().rstrip(";").strip()
    upper = cleaned.upper()
    # Reject SQL comments outright: a legitimate generated SELECT never needs
    # them, and they are the classic vehicle for smuggling payloads past a
    # token scan (e.g. hiding a chained statement behind `--`).
    if "--" in cleaned or "/*" in cleaned:
        return False, "SQL comments are not permitted.", cleaned
    if not upper.startswith("SELECT") and not upper.startswith("WITH"):
        return False, f"Generated SQL must start with SELECT/WITH: {sql[:80]}", cleaned
    if ";" in cleaned:
        return False, "Statement chaining is not permitted.", cleaned
    tokens = set(_TOKEN_RE.findall(upper))
    blocked = tokens & (FORBIDDEN_SQL_KEYWORDS | FORBIDDEN_SQL_FUNCTIONS)
    if blocked:
        return False, f"Forbidden keyword(s) in generated SQL: {', '.join(sorted(blocked))}", cleaned
    return True, None, cleaned


# ─── LLM invocation ─────────────────────────────────────────────────────────

def generate_sql_from_question(question: str) -> str:
    """Call the configured LLM endpoint and return raw SQL.

    Resolves the active provider from `data/.llm_config.json`; falls back to
    localhost Ollama at port 11434 with model `llama3` if the config is
    missing or unreadable. Strips markdown code fences if the LLM emits them
    despite being told not to.
    """
    cfg: dict = {}
    try:
        from core.api.llm_config import load_config, resolve_endpoints
        cfg = load_config()
        gen_url, _, headers = resolve_endpoints(cfg)
    except Exception:
        gen_url = "http://localhost:11434/api/generate"
        headers = {}
        cfg = {"model": "llama3", "temperature": 0.1, "timeout": 30}

    prompt = f"{NL2SQL_SYSTEM}\n\nQuestion: {question}\n\nSQL:"
    payload = {
        "model":   cfg.get("model", "llama3"),
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.1, "num_ctx": 4096},
    }

    import requests as _req
    resp = _req.post(
        gen_url, json=payload, headers=headers,
        timeout=int(cfg.get("timeout", 30)),
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "").strip()

    # Strip markdown fences if the LLM ignored the no-markdown instruction.
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.lower().startswith("sql"):
            raw = raw[3:]
        raw = raw.strip().rstrip("```").strip()

    return raw.strip()
