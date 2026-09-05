import pytest

from app.discovery.scoring import ScoreComponents, opportunity_score, sponsorship_score


@pytest.mark.parametrize(
    ("cases", "expected"),
    [(0, 0), (1, 25), (4, 25), (5, 50), (24, 50), (25, 75), (99, 75), (100, 100)],
)
def test_sponsorship_bands(cases: int, expected: int) -> None:
    assert sponsorship_score(cases, resolved=True) == expected


def test_unresolved_sponsorship_is_zero_even_with_cases() -> None:
    assert sponsorship_score(100, resolved=False) == 0


def test_opportunity_score_uses_documented_weights_and_half_up_rounding() -> None:
    components = ScoreComponents(
        industry=90,
        sponsorship=25,
        internship=100,
        monitorability=100,
        current_openings=0,
    )
    assert opportunity_score(components) == 65
