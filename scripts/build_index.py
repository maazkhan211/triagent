"""
Builds/refreshes the ChromaDB similarity index from data/historical_incidents.json.
This is the "knowledge base" the triage agent searches for similar past incidents.

Run:
    python scripts/build_index.py            # add/refresh the historical incidents
    python scripts/build_index.py --reset    # wipe the index first, then rebuild

Use --reset after demos or test runs: every triage saves itself back into the
index, so repeated testing leaves near-duplicates of your own sample logs that
then surface as spurious ~1.00 similarity matches.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage_agent.config import HISTORICAL_DATA_PATH
from triage_agent.vectorstore import add_incident, count, reset_collection


def main():
    with open(HISTORICAL_DATA_PATH, encoding="utf-8") as f:
        records = json.load(f)

    if "--reset" in sys.argv:
        print("Resetting the collection first (--reset)...")
        reset_collection()

    print(f"Indexing {len(records)} historical incidents into ChromaDB...")
    start = time.time()
    for i, record in enumerate(records, 1):
        add_incident(record)
        print(f"  [{i}/{len(records)}] {record['log_id']} ({record['error_type']})")
    elapsed = time.time() - start

    print(f"Done in {elapsed:.1f}s. Collection now has {count()} incidents.")


if __name__ == "__main__":
    main()
