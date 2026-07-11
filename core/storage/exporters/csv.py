import pandas as pd

def export_to_csv(db_path: str, output_path: str):
    """
    Export database to CSV.
    """
    from ..db import get_connection
    conn = get_connection(db_path)
    try:
        df = pd.read_sql_query("SELECT * FROM containers LEFT JOIN shipments ON containers.shipment_id = shipments.id", conn)
        df.to_csv(output_path, index=False)
    finally:
        conn.close()
