"""Build constrained candidates from recorded history. The model cannot choose its task."""

from audit_prep.domain import (
    AuditContext,
    EvidenceChunk,
    InterpretationCandidate,
    InterpretationRequest,
    ModelAttentionResponse,
    RecordedFact,
    SchemeContext,
)
from audit_prep.model_provider import InterpretationModel, ModelProviderError


def build_interpretation_candidates(
    previous: AuditContext | None,
    current: AuditContext,
) -> list[InterpretationCandidate]:
    if previous is None:
        return []

    findings = {finding.id: finding for finding in previous.findings}
    candidates = []
    for action in sorted(previous.corrective_actions, key=lambda action: action.id):
        finding = findings[action.finding_id]
        if action.status != "closed" or finding.topic != "supplier_traceability":
            continue

        candidates.append(
            InterpretationCandidate(
                id=action.id,
                topic=finding.topic,
                query="supplier suppliers subcontracted verification traceability register",
                corrective_action_document_id=action.document_id,
                recorded_facts=[
                    RecordedFact(
                        fact_type="corrective_action_status",
                        entity_id=action.id,
                        value=action.status,
                        source="structured_audit_data",
                    ),
                    RecordedFact(
                        fact_type="historical_finding",
                        entity_id=finding.id,
                        value=finding.description,
                        source="structured_audit_data",
                    ),
                    RecordedFact(
                        fact_type="current_audit",
                        entity_id=current.audit_id,
                        value=current.planned_date.isoformat()
                        if current.planned_date
                        else "unplanned",
                        source="structured_audit_data",
                    ),
                ],
            )
        )

    return candidates


class AttentionInterpreter:
    def __init__(self, model: InterpretationModel):
        self.model = model

    def interpret(
        self,
        candidate: InterpretationCandidate,
        evidence: list[EvidenceChunk],
        scheme: SchemeContext,
    ) -> ModelAttentionResponse:
        request = InterpretationRequest(
            candidate_id=candidate.id,
            candidate_type=candidate.candidate_type,
            topic=candidate.topic,
            recorded_facts=candidate.recorded_facts,
            evidence=evidence,
            scheme=scheme,
        )
        if len(evidence) > 24 or len(request.model_dump_json()) > 32000:
            raise ModelProviderError("interpretation_context_limit")

        return self.model.generate(request)
