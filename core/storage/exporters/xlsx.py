import pandas as pd

def export_to_xlsx(db_path: str, output_path: str, columns_config: list):
    """
    Export database to XLSX using the new architecture.
    """
    from ..db import get_connection
    conn = get_connection(db_path)
    try:
        # Load data (this query will be adjusted based on the module)
        # For now, placeholder query
        df = pd.read_sql_query("SELECT * FROM containers LEFT JOIN shipments ON containers.shipment_id = shipments.id", conn)
        df.to_excel(output_path, index=False)
    finally:
        conn.close()
