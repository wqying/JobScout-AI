import json
from pathlib import Path

DATASET = Path(__file__).parents[4] / "evals" / "datasets" / "industry_discovery_v1.jsonl"


def test_initial_discovery_dataset_has_twenty_reviewable_industries() -> None:
    rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    assert len(rows) >= 20
    assert all(row["query"] and row["expected_segments"] for row in rows)
    assert all(len(row["reviewed_companies"]) >= 2 for row in rows)
