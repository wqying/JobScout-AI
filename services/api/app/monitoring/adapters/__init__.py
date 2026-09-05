from __future__ import annotations

from app.monitoring.adapters.ashby import AshbyAdapter
from app.monitoring.adapters.generic_html import GenericHtmlAdapter
from app.monitoring.adapters.greenhouse import GreenhouseAdapter
from app.monitoring.adapters.lever import LeverAdapter
from app.monitoring.adapters.smartrecruiters import SmartRecruitersAdapter
from app.monitoring.http import HttpFetcher
from app.monitoring.schemas import CareerSourceAdapter


def adapter_for(provider: str, http: HttpFetcher) -> CareerSourceAdapter:
    if provider == "greenhouse":
        return GreenhouseAdapter(http)
    if provider == "lever":
        return LeverAdapter(http)
    if provider == "ashby":
        return AshbyAdapter(http)
    if provider == "smartrecruiters":
        return SmartRecruitersAdapter(http)
    if provider == "generic_html":
        return GenericHtmlAdapter(http)
    raise ValueError("UNSUPPORTED_CAREER_SOURCE")


__all__ = [
    "AshbyAdapter",
    "GenericHtmlAdapter",
    "GreenhouseAdapter",
    "LeverAdapter",
    "SmartRecruitersAdapter",
    "adapter_for",
]
