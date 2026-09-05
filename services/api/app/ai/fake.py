from __future__ import annotations

from copy import deepcopy

from app.ai.client import AIUsage, NormalizationResponse, ResearchResponse
from app.ai.schemas.discovery import NormalizedDiscovery


class FakeDiscoveryAIClient:
    """Credential-free deterministic fake used by tests and eval fixtures."""

    research_model = "fake-research-v1"
    structured_model = "fake-structured-v1"

    def __init__(
        self,
        discovery: NormalizedDiscovery,
        source_manifest: list[dict[str, str | None]],
    ) -> None:
        self.discovery = discovery
        self.source_manifest = source_manifest
        self.research_calls = 0
        self.normalization_calls = 0
        self.research_exclusions: list[list[dict[str, str]]] = []

    async def research(
        self,
        query: str,
        country: str,
        excluded_companies: list[dict[str, str]] | None = None,
    ) -> ResearchResponse:
        self.research_calls += 1
        self.research_exclusions.append(deepcopy(excluded_companies or []))
        return ResearchResponse(
            report=f"Fixture-backed research for {query} in {country}.",
            source_manifest=deepcopy(self.source_manifest),
            usage=AIUsage(input_tokens=120, output_tokens=350, web_search_calls=1),
        )

    async def normalize(
        self,
        report: str,
        source_manifest: list[dict[str, str | None]],
        repair_feedback: str | None = None,
    ) -> NormalizationResponse:
        del report, source_manifest, repair_feedback
        self.normalization_calls += 1
        raw = self.discovery.model_dump(mode="json")
        return NormalizationResponse(
            discovery=NormalizedDiscovery.model_validate(raw),
            raw_output=raw,
            usage=AIUsage(input_tokens=600, output_tokens=800),
        )
