"""Regression tests for batch-4 fixes (broken exporters, test infra)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _seed_logistics(db):
    from core.storage.db import init_schema, get_connection
    init_schema(db)
    conn = get_connection(db)
    try:
        conn.execute("INSERT INTO shipments (tan, vessel) VALUES ('TAN/1/2026', 'MSC A')")
        sid = conn.execute("SELECT id FROM shipments").fetchone()[0]
        conn.execute("INSERT INTO containers (shipment_id, container_number) VALUES (?, 'MSCU1234567')", (sid,))
        conn.commit()
    finally:
        conn.close()


def test_csv_exporter_import_depth_fixed(tmp_path):
    """The exporter used `from .db import` (nonexistent) — must resolve now."""
    from core.storage.exporters.csv import export_to_csv
    db = str(tmp_path / "logistics.db")
    out = str(tmp_path / "out.csv")
    _seed_logistics(db)
    export_to_csv(db, out)
    assert os.path.exists(out)
    assert "MSCU1234567" in open(out).read()


def test_xlsx_exporter_import_depth_fixed(tmp_path):
    from core.storage.exporters.xlsx import export_to_xlsx
    db = str(tmp_path / "logistics.db")
    out = str(tmp_path / "out.xlsx")
    _seed_logistics(db)
    export_to_xlsx(db, out, columns_config=[])
    assert os.path.exists(out) and os.path.getsize(out) > 0
