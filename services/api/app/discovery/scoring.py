from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class ScoreComponents:
    industry: int
    sponsorship: int
    internship: int
    monitorability: int
    current_openings: int

    def __post_init__(self) -> None:
        for value in (
            self.industry,
            self.sponsorship,
            self.internship,
            self.monitorability,
            self.current_openings,
        ):
            if not 0 <= value <= 100:
                raise ValueError("All opportunity score components must be between 0 and 100")


def sponsorship_score(certified_cases: int, *, resolved: bool) -> int:
    if not resolved or certified_cases <= 0:
        return 0
    if certified_cases < 5:
        return 25
    if certified_cases < 25:
        return 50
    if certified_cases < 100:
        return 75
    return 100


def opportunity_score(components: ScoreComponents) -> int:
    weighted = (
        Decimal(components.industry) * Decimal("0.30")
        + Decimal(components.sponsorship) * Decimal("0.30")
        + Decimal(components.internship) * Decimal("0.20")
        + Decimal(components.monitorability) * Decimal("0.10")
        + Decimal(components.current_openings) * Decimal("0.10")
    )
    return int(weighted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
