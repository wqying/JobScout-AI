import json

import httpx

from app.ai.client import OpenAIResponsesClient, _openai_discovery_schema
from app.ai.schemas.discovery import NormalizedDiscovery


async def test_two_response_calls_are_bounded_stateless_and_structured() -> None:
    requests: list[dict[str, object]] = []
    request_paths: list[str] = []
    normalized = NormalizedDiscovery.model_validate(
        {
            "interpretation": {
                "normalized_label": "Gaming",
                "slug": "gaming",
                "included_segments": ["video games"],
                "excluded_segments": [],
                "target_role_families": ["software"],
                "country_code": "US",
            },
            "companies": [],
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "web_search_call",
                            "action": {
                                "sources": [{"url": "https://games.example", "title": "Games"}]
                            },
                        },
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "Research report"}],
                        },
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 200},
                },
            )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": normalized.model_dump_json(),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 300, "output_tokens": 100},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openai.test/v1"
    ) as http_client:
        client = OpenAIResponsesClient(
            api_key="test-key",
            research_model="research-model",
            structured_model="structured-model",
            max_web_search_calls=3,
            http_client=http_client,
        )
        research = await client.research(
            "gaming companies",
            "US",
            excluded_companies=[
                {"canonical_name": "Existing Games", "official_domain": "existing.example"}
            ],
        )
        result = await client.normalize(research.report, research.source_manifest)

    assert research.source_manifest[0]["url"] == "https://games.example"
    assert research.source_manifest[0]["source_id"] == "source_1"
    assert research.usage.web_search_calls == 1
    assert result.discovery.interpretation.slug == "gaming"
    assert requests[0]["store"] is False
    assert requests[0]["max_tool_calls"] == 3
    assert requests[0]["include"] == ["web_search_call.action.sources"]
    assert "existing.example" in str(requests[0]["input"])
    assert "tools" not in requests[1]
    assert '"source_id": "source_1"' in str(requests[1]["input"])
    text_format = requests[1]["text"]
    assert isinstance(text_format, dict)
    assert text_format["format"]["strict"] is True
    assert all(request["model"] for request in requests)
    assert request_paths == ["/v1/responses", "/v1/responses"]


def test_structured_output_schema_omits_unsupported_uri_format() -> None:
    schema = _openai_discovery_schema()
    schema_text = json.dumps(schema)

    assert '"format": "uri"' not in schema_text
    assert '"official_website_url"' in schema_text
    assert '"official_careers_source_id"' in schema_text
    assert '"official_careers_url"' not in schema_text
    assert '"cpt_evidence_status"' not in schema_text
    assert "official_careers_source_id" in schema["$defs"]["CompanyProposal"]["required"]


async def test_openai_error_includes_safe_diagnostic_fields() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={"x-request-id": "req-test"},
            json={
                "error": {
                    "message": "Unsupported format: uri",
                    "type": "invalid_request_error",
                    "code": "invalid_json_schema",
                    "param": "text.format.schema",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openai.test/v1"
    ) as http_client:
        client = OpenAIResponsesClient(
            api_key="test-key",
            research_model="research-model",
            structured_model="structured-model",
            http_client=http_client,
        )
        try:
            await client.research("gaming companies", "US")
        except RuntimeError as exc:
            message = str(exc)
        else:  # pragma: no cover - protects the test's intent
            raise AssertionError("Expected the mocked OpenAI request to fail")

    assert "HTTP 400" in message
    assert "req-test" in message
    assert "Unsupported format: uri" in message
    assert "invalid_json_schema" in message
