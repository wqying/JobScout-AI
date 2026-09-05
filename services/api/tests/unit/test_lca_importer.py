from pathlib import Path

import pytest

from app.immigration.importer import LcaImportError, aggregate_lca_file

FIXTURES = Path(__file__).parents[1] / "fixtures" / "dol"


def test_synthetic_lca_fixture_aggregates_normalized_employers() -> None:
    rows_read, rows_accepted, aggregates = aggregate_lca_file(FIXTURES / "lca_synthetic.csv")

    assert rows_read == 4
    assert rows_accepted == 4
    assert set(aggregates) == {"acme games", "other studio"}
    assert aggregates["acme games"].certified_cases == 2
    assert aggregates["acme games"].certified_workers == 3
    assert aggregates["acme games"].denied_cases == 1
    assert aggregates["acme games"].withdrawn_cases == 1


def test_missing_required_columns_fail_safely(tmp_path: Path) -> None:
    fixture = tmp_path / "bad.csv"
    fixture.write_text("COMPANY,RESULT\nAcme,CERTIFIED\n")

    with pytest.raises(LcaImportError) as exc_info:
        aggregate_lca_file(fixture)

    assert exc_info.value.code == "MISSING_REQUIRED_COLUMNS"
