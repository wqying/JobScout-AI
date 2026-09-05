from decimal import ROUND_HALF_UP, Decimal

from app.ai.client import AIUsage


def estimate_cost_usd(
    usage: AIUsage,
    *,
    input_per_million: Decimal,
    output_per_million: Decimal,
    web_search_per_call: Decimal,
) -> Decimal:
    token_cost = (
        Decimal(usage.input_tokens) * input_per_million
        + Decimal(usage.output_tokens) * output_per_million
    ) / Decimal(1_000_000)
    search_cost = Decimal(usage.web_search_calls) * web_search_per_call
    return (token_cost + search_cost).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
