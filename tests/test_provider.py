import json

import pytest

from audit_prep.domain import InterpretationRequest, ModelAttentionResponse
from audit_prep.interpretation import AttentionInterpreter, build_interpretation_candidates
from audit_prep.loading import load_case
from audit_prep.model_provider import (
    AzureOpenAIInterpretationModel,
    FakeInterpretationModel,
    ModelProviderError,
)
from audit_prep.retrieval import chunk_document


def no_attention_response() -> ModelAttentionResponse:
    return ModelAttentionResponse(
        result="no_attention_item",
        type=None,
        evidence_chunk_ids=[],
        quotes=[],
        interpretation=None,
        uncertainty=[],
        missing_information=[],
        suggested_follow_up=None,
        abstention_reason=None,
    )


def request() -> InterpretationRequest:
    previous, current, documents = load_case()
    candidate = build_interpretation_candidates(previous, current)[0]

    return InterpretationRequest(
        candidate_id=candidate.id,
        candidate_type=candidate.candidate_type,
        topic=candidate.topic,
        recorded_facts=candidate.recorded_facts,
        evidence=chunk_document(documents[0]) + chunk_document(documents[1]),
        scheme=current.scheme,
    )


def test_fake_records_exact_constrained_request():
    previous, current, documents = load_case()
    candidate = build_interpretation_candidates(previous, current)[0]
    model = FakeInterpretationModel({candidate.id: no_attention_response()})
    AttentionInterpreter(model).interpret(candidate, chunk_document(documents[0]), current.scheme)
    assert len(model.requests) == 1
    assert {chunk.document_id for chunk in model.requests[0].evidence} == {"DOC-CA214"}
    assert model.name == "offline_fixture"


def test_missing_configuration_and_invalid_endpoint(monkeypatch):
    for name in ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="Set AZURE"):
        AzureOpenAIInterpretationModel.from_environment()

    for endpoint in [
        "http://test.openai.azure.com/openai/v1/",
        "https://evil.example/openai/v1/",
        "https://test.openai.azure.com/openai/v1/?key=secret",
    ]:
        with pytest.raises(ValueError, match="HTTPS Azure"):
            AzureOpenAIInterpretationModel(endpoint, "deployment", "secret")


@pytest.mark.parametrize("failure", [None, "refusal", "length", "invalid_json", "http_error"])
def test_azure_sdk_contract_using_local_http_transport(failure):
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("openai")
    calls = []

    def respond(http_request):
        calls.append(json.loads(http_request.content))

        if failure == "http_error":
            return httpx.Response(500, json={"error": {"message": "SECRET REQUEST BODY"}})

        content = (
            "bad json" if failure == "invalid_json" else no_attention_response().model_dump_json()
        )

        return httpx.Response(
            200,
            json={
                "id": "fixture-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "test-deployment",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "length" if failure == "length" else "stop",
                        "message": {
                            "role": "assistant",
                            "content": content,
                            "refusal": "Refused" if failure == "refusal" else None,
                        },
                    }
                ],
            },
        )

    model = AzureOpenAIInterpretationModel(
        "https://test.openai.azure.com/openai/v1/", "test-deployment", "test-key"
    )
    model._client = model._client.with_options(
        http_client=httpx.Client(transport=httpx.MockTransport(respond))
    )

    if failure:
        with pytest.raises(ModelProviderError) as error:
            model.generate(request())

        assert "SECRET" not in str(error.value)
    else:
        assert model.generate(request()).result == "no_attention_item"

    assert len(calls) == 1
    assert calls[0]["model"] == "test-deployment"
    assert calls[0]["store"] is False
    assert calls[0]["response_format"]["json_schema"]["strict"] is True


def test_wire_schema_requires_quotes():
    schema = ModelAttentionResponse.model_json_schema()
    assert "quotes" in schema["required"]
    assert set(schema["$defs"]["ModelQuote"]["required"]) == {"chunk_id", "text"}
