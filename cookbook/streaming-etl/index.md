# Streaming ETL Pipeline

Process millions of database rows without loading everything into memory:

```
import json
from pyarallel import parallel_iter

def fetch_rows():
    """Yield rows from a database cursor — never loads full result set."""
    cursor.execute("SELECT * FROM events WHERE processed = false")
    while batch := cursor.fetchmany(1000):
        yield from batch

def transform(row):
    return {
        "event_id": row["id"],
        "timestamp": row["created_at"].isoformat(),
        "payload": json.loads(row["raw_data"]),
    }

output_buffer = []
for item in parallel_iter(transform, fetch_rows(), workers=8, window_size=500):
    if item.ok:
        output_buffer.append(item.value)
        if len(output_buffer) >= 1000:
            write_parquet_batch(output_buffer)
            output_buffer.clear()
    else:
        log_error(item.index, item.error)
```
