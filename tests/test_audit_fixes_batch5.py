"""Regression test for batch-5: batched free_days lookup (swimlane N+1 fix)."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.storage.db import init_schema, get_connection
from core.business.demurrage import free_days_map, free_days_from_documents


def _seed(db):
    init_schema(db)
    conn = get_connection(db)
    try:
        for tan, fd in [("TAN/1/2026", 21), ("TAN/2/2026", 7)]:
            conn.execute(
                "INSERT INTO documents (type, module, extracted_json) VALUES ('BOOKING','logistics',?)",
                (json.dumps({"tan_number": tan, "free_days": fd}),),
            )
        # a TAN with no free_days
        conn.execute("INSERT INTO documents (type, module, extracted_json) VALUES ('BOOKING','logistics',?)",
                     (json.dumps({"tan_number": "TAN/3/2026"}),))
        conn.commit()
    finally:
        conn.close()


def test_free_days_map_matches_single_lookup(tmp_path):
    db = str(tmp_path / "logistics.db")
    _seed(db)
    tans = {"TAN/1/2026", "TAN/2/2026", "TAN/3/2026", "TAN/missing"}
    batched = free_days_map(db, tans)
    assert batched == {"TAN/1/2026": 21, "TAN/2/2026": 7}
    # parity with the per-TAN function
    for t in tans:
        assert batched.get(t) == free_days_from_documents(db, t)


def test_free_days_map_empty_inputs(tmp_path):
    db = str(tmp_path / "logistics.db")
    _seed(db)
    assert free_days_map(db, set()) == {}
    assert free_days_map(str(tmp_path / "nope.db"), {"TAN/1/2026"}) == {}
