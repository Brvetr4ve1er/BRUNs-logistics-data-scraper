"""Database migrations for the platform.

Versioned, idempotent SQLite migrations. Each migration is identified by a
string ID and recorded in the `schema_version` table once applied.

Idempotency strategy: SQLite does NOT support `ALTER TABLE ADD COLUMN IF NOT
EXISTS`. We catch the well-known `OperationalError` messages that mean
"already done" ("duplicate column name", "table … already exists",
"index … already exists") and treat them as successful no-ops.

Public API:
    run_migrations(db_path: str) -> list[str]
        Apply any pending migrations. Returns list of newly applied IDs.
        Safe to call on every server start.
"""
import logging
import sqlite3

log = logging.getLogger(__name__)

# ─── Migration registry ──────────────────────────────────────────────────────
# Each entry: (migration_id, [sql_statement, ...])
# Splitting compound DDL into separate statements keeps individual failures
# from cascading.
MIGRATIONS: list[tuple[str, list[str]]] = [
    ("001_docs_reviewed_fields", [
        "ALTER TABLE documents ADD COLUMN reviewed_at TEXT",
        "ALTER TABLE documents ADD COLUMN reviewed_by TEXT",
    ]),
    ("002_families_case_management", [
        "ALTER TABLE families ADD COLUMN case_status TEXT DEFAULT 'COLLECTING'",
        "ALTER TABLE families ADD COLUMN next_action TEXT",
        "ALTER TABLE families ADD COLUMN next_action_date TEXT",
    ]),
    ("003_documents_travel_original_doc_id", [
        "ALTER TABLE documents_travel ADD COLUMN original_doc_id INTEGER",
    ]),
    ("004_indexes_for_dashboard_queries", [
        "CREATE INDEX IF NOT EXISTS idx_docs_module ON documents(module)",
        "CREATE INDEX IF NOT EXISTS idx_docs_confidence ON documents(confidence)",
        "CREATE INDEX IF NOT EXISTS idx_docs_reviewed_at ON documents(reviewed_at)",
        "CREATE INDEX IF NOT EXISTS idx_shipments_eta ON shipments(eta)",
        "CREATE INDEX IF NOT EXISTS idx_shipments_status ON shipments(status)",
        "CREATE INDEX IF NOT EXISTS idx_containers_status ON containers(statut_container)",
        "CREATE INDEX IF NOT EXISTS idx_persons_normalized ON persons(normalized_name)",
        "CREATE INDEX IF NOT EXISTS idx_documents_travel_expiry ON documents_travel(expiry_date)",
    ]),
    ("005_matches_table", [
        # For fuzzy identity resolution review queue (TD5 wiring)
        """CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_a_id INTEGER,
            entity_b_id INTEGER,
            score REAL,
            status TEXT DEFAULT 'PENDING',
            resolved_by TEXT,
            resolved_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
    ]),
    ("006_shipments_tan_index", [
        # tan is the upsert key for every logistics projection and the lookup
        # key for demurrage/free-days queries — index it. A UNIQUE index is a
        # deliberate follow-up: it would fail on any pre-existing duplicate tan,
        # and new duplicates are already prevented by the projection's
        # find-by-tan-before-insert guard.
        "CREATE INDEX IF NOT EXISTS idx_shipments_tan ON shipments(tan)",
    ]),
]

# Error fragments that mean "the change is already applied" — treat as success.
_ALREADY_APPLIED = (
    "duplicate column name",
    "already exists",
)


def _is_already_applied(err: Exception) -> bool:
    msg = str(err).lower()
    return any(frag in msg for frag in _ALREADY_APPLIED)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def run_migrations(db_path: str) -> list[str]:
    """Apply any pending migrations to `db_path`. Returns list of newly applied IDs."""
    from .db import get_connection
    conn = get_connection(db_path)
    applied_now: list[str] = []
    try:
        # Bootstrap: schema_version tracks which migrations have run.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id TEXT PRIMARY KEY,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        already_applied = {
            r[0] for r in conn.execute("SELECT id FROM schema_version").fetchall()
        }

        for mid, statements in MIGRATIONS:
            if mid in already_applied:
                continue

            critical_failure = False
            for stmt in statements:
                stmt = stmt.strip()
                if not stmt:
                    continue
                # Some migrations only make sense if a parent table exists.
                # Skip ALTER TABLE on missing tables silently (e.g., a fresh
                # install that doesn't yet have a `documents_travel` table
                # because the travel side hasn't been initialised).
                if stmt.upper().startswith("ALTER TABLE"):
                    parts = stmt.split()
                    if len(parts) >= 3 and not _table_exists(conn, parts[2]):
                        continue
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    if _is_already_applied(e):
                        # Idempotent: column/table/index already there
                        continue
                    # CREATE INDEX is best-effort — a missing column on a
                    # foreign DB (e.g. travel.db has a stale containers table)
                    # is a known false-positive, skip silently.
                    if stmt.upper().startswith("CREATE INDEX"):
                        continue
                    # Schema-affecting error — bail and let the next run retry.
                    log.error("migration %s failed on stmt: %s", mid, e)
                    log.error("  stmt was: %s", stmt[:120])
                    critical_failure = True
                    break

            if not critical_failure:
                conn.execute(
                    "INSERT INTO schema_version (id) VALUES (?)", (mid,)
                )
                conn.commit()
                applied_now.append(mid)
    finally:
        conn.close()
    return applied_now
