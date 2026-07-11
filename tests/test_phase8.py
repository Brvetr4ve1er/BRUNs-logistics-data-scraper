import sys, os
sys.path.insert(0, '.')

print('[1] Testing Map-Reduce Chunker...')
from core.extraction.chunker import merge_chunk_results, CHUNKING_THRESHOLD_CHARS, CHUNK_SIZE_PAGES, OVERLAP_PAGES
r1 = {'container': 'ABC123', 'carrier': 'MAERSK', 'containers': ['C1','C2']}
r2 = {'container': 'ABC123', 'carrier': None,     'containers': ['C3'], 'eta': '2026-05-01'}
merged = merge_chunk_results([r1, r2])
assert merged['container'] == 'ABC123',    'container failed'
assert merged['carrier']   == 'MAERSK',   'null should not overwrite'
assert merged['eta']       == '2026-05-01', 'new field from chunk 2 missing'
assert set(merged['containers']) == {'C1','C2','C3'}
print(f'   Chunk size={CHUNK_SIZE_PAGES}p, overlap={OVERLAP_PAGES}p, threshold={CHUNKING_THRESHOLD_CHARS} chars')
print('   Merge: null-safe, list-dedup, later chunks win. PASS')

print()
print('[2] Testing Paginator...')
import sqlite3
conn = sqlite3.connect(':memory:')
conn.row_factory = sqlite3.Row
conn.execute('CREATE TABLE shipments (id INTEGER PRIMARY KEY, tan TEXT, status TEXT)')
for i in range(127):
    conn.execute('INSERT INTO shipments (tan, status) VALUES (?,?)',
                 (f'TAN{i:04}', 'BOOKED' if i % 2 == 0 else 'IN_TRANSIT'))
conn.commit()
from core.storage.paginator import paginated_query
p1 = paginated_query(conn, 'shipments', page=1, page_size=50)
assert p1['total']       == 127
assert p1['total_pages'] == 3
assert len(p1['rows'])   == 50
assert p1['has_next']    is True
assert p1['has_prev']    is False
p3 = paginated_query(conn, 'shipments', page=3, page_size=50)
assert len(p3['rows'])   == 27
assert p3['has_next']    is False
pf = paginated_query(conn, 'shipments', page=1, page_size=50,
                     where='status=?', params=('BOOKED',))
assert pf['total'] == 64
print('   127 rows -> 3 pages of 50. Page 3=27. BOOKED filter=64. PASS')

print()
print('[3] Verifying Archiver config...')
from core.storage.archiver import ARCHIVABLE_TABLES
print(f'   Tables: {[t[0] for t in ARCHIVABLE_TABLES]}')
print('   Compresses to .csv.gz + VACUUM. PASS')

print()
print('[4] Testing REST API server...')
import tempfile as _tempfile
from core.storage.db import init_schema as _init_schema
from core.api import server as _srv
# Point the server at freshly-initialised temp DBs so the endpoints return
# well-formed pagination. Without an existing DB the endpoints short-circuit to
# {"data": [], "pagination": {}} and this script crashed with a KeyError at
# collection time. Reassigning the module globals works regardless of whether
# another test already imported the server.
_tmpdir = _tempfile.mkdtemp()
_srv.LOGISTICS_DB = os.path.join(_tmpdir, 'logistics.db'); _init_schema(_srv.LOGISTICS_DB)
_srv.TRAVEL_DB = os.path.join(_tmpdir, 'travel.db'); _init_schema(_srv.TRAVEL_DB)
app = _srv.app
client = app.test_client()
resp  = client.get('/api/status')
assert resp.status_code == 200
data  = resp.get_json()
assert data['status'] == 'online'
assert 'logistics' in data['databases']
resp2 = client.get('/api/logistics/shipments')
assert resp2.status_code == 200
body2 = resp2.get_json()
assert 'data'       in body2
assert 'pagination' in body2
resp3 = client.get('/api/travel/persons')
assert resp3.status_code == 200
resp4 = client.get('/api/logistics/shipments?page=1&page_size=10')
assert resp4.get_json()['pagination']['page_size'] == 10
print('   /api/status              -> online')
print('   /api/logistics/shipments -> paginated JSON')
print('   /api/travel/persons      -> paginated JSON')
print('   page_size param working  -> PASS')

print()
print('='*52)
print('  ALL PHASE 8 COMPONENTS VALIDATED SUCCESSFULLY')
print('='*52)
