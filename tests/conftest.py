import pytest

from audit_prep.domain import (
    AttentionType,
    AuthorizationContext,
    ModelAttentionResponse,
    ModelQuote,
)
from audit_prep.interpretation import build_interpretation_candidates
from audit_prep.loading import load_case
from audit_prep.model_provider import FakeInterpretationModel
from audit_prep.retrieval import chunk_document


@pytest.fixture
def case():
    previous, current, documents = load_case()
    auth = AuthorizationContext(
        tenant_id=current.tenant_id, allowed_audit_ids={previous.audit_id, current.audit_id}
    )

    return previous, current, documents, auth


@pytest.fixture
def candidate(case):
    return build_interpretation_candidates(case[0], case[1])[0]


@pytest.fixture
def evidence(case):
    return chunk_document(case[2][0]) + chunk_document(case[2][1])


@pytest.fixture
def response(evidence):
    cited = [
        next(
            chunk
            for chunk in evidence
            if chunk.document_id == document_id and "Supplier Verification Register" in chunk.text
        )
        for document_id in ["DOC-CA214", "DOC-SUPPLIER-PROC-V4"]
    ]

    return ModelAttentionResponse(
        result="attention_item",
        type=AttentionType.POSSIBLE_INCONSISTENCY,
        evidence_chunk_ids=[chunk.id for chunk in cited],
        quotes=[
            ModelQuote(chunk_id=cited[0].id, text="all direct and subcontracted suppliers"),
            ModelQuote(
                chunk_id=cited[1].id,
                text="All direct suppliers must be entered into the Supplier Verification Register",
            ),
        ],
        interpretation="The procedure may not fully reflect the documented scope of CA-214.",
        uncertainty=[
            "Available documents do not establish operational handling of subcontractors."
        ],
        missing_information=[],
        suggested_follow_up="Verify how subcontracted suppliers are currently handled.",
        abstention_reason=None,
    )


def sent_requests(app):
    model = app.interpreter.model
    assert isinstance(model, FakeInterpretationModel)

    return model.requests
