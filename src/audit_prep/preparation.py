"""The single application flow: facts, authorized evidence, constrained suggestion, validation."""

from pydantic import ValidationError

from audit_prep.domain import (
    Abstention,
    AbstentionReason,
    AIState,
    AttentionItem,
    AuditContext,
    AuthorizationContext,
    Document,
    EvidenceQuote,
    InterpretationCandidate,
    ModelAttentionResponse,
    PreparationResult,
    ProcessingMetadata,
    evidence_reference,
)
from audit_prep.fingerprint import run_fingerprint
from audit_prep.interpretation import AttentionInterpreter, build_interpretation_candidates
from audit_prep.model_provider import ModelProviderError
from audit_prep.retrieval import (
    EvidenceRetriever,
    LexicalEvidenceRetriever,
    authorized_documents,
    chunk_document,
)
from audit_prep.rules import PreparationRuleEngine
from audit_prep.validation import AttentionValidator

LIMITATIONS = [
    "Synthetic data and a fictional scheme. Suggestions require auditor review.",
    "Lexical retrieval completeness applies to selected documents, not the full evidence universe.",
    "Reference validation does not prove semantic entailment. English claim guards are incomplete.",
    "Quote checks confirm wording in cited passages, not that the interpretation is correct.",
    "Injection screening is a heuristic. Withheld passages need manual review.",
    "Missing evidence means not found in authorized material, not proof of missing activity.",
    "Authorization is supplied by the caller. This prototype does not implement authentication.",
    "Only historical supplier_traceability actions are investigated in this constrained slice.",
]


def _validate_context(
    previous: AuditContext | None,
    current: AuditContext,
    auth: AuthorizationContext,
) -> None:
    for audit in [current, previous]:
        if audit and (
            audit.tenant_id != auth.tenant_id or audit.audit_id not in auth.allowed_audit_ids
        ):
            raise PermissionError("Audit context is not authorized")

    if previous:
        if previous.organization_id != current.organization_id:
            raise ValueError("Audit history must belong to the same organization")

        if previous.audit_id == current.audit_id:
            raise ValueError("Previous and current audits must be distinct")

        if previous.scheme.id != current.scheme.id:
            raise ValueError("Audit history must belong to the same scheme")


def _ai_state(interpreter: AttentionInterpreter | None, auth: AuthorizationContext) -> AIState:
    if not auth.ai_enabled:
        return "disabled_by_tenant"

    return "enabled" if interpreter else "not_configured"


def _candidate_documents(
    candidate: InterpretationCandidate,
    previous: AuditContext | None,
    current: AuditContext,
    documents: list[Document],
) -> tuple[list[Document], str | None]:
    if previous is None:
        return [], "A previous audit is required for comparison."

    historical = [
        doc
        for doc in documents
        if doc.audit_id == previous.audit_id
        and doc.document_type == "corrective_action"
        and doc.id == candidate.corrective_action_document_id
    ]

    procedures = [
        doc
        for doc in documents
        if doc.audit_id == current.audit_id
        and doc.document_type == "supplier_procedure"
        and (doc.site_id is None or doc.site_id in current.scope.sites)
    ]

    if not current.planned_date or previous.scheme.version != current.scheme.version:
        return (
            historical,
            "A planned date and matching scheme versions are required for comparison.",
        )

    if previous.audit_date and previous.audit_date >= current.planned_date:
        return historical, "Previous audit date must precede the current planned audit."

    action = next(action for action in previous.corrective_actions if action.id == candidate.id)
    if not action.closed_at or action.closed_at > current.planned_date:
        return historical, "Corrective action closure must be dated before the planned audit."

    if any(not doc.effective_date or not doc.version for doc in procedures):
        return historical, "Current supplier procedure version or effective date is missing."

    if any(doc.effective_date and doc.effective_date > current.planned_date for doc in historical):
        return [], "Corrective action evidence is dated after the planned audit."

    procedures = [
        doc
        for doc in procedures
        if doc.effective_date and doc.effective_date <= current.planned_date
    ]

    active = []
    for site in sorted({doc.site_id or "" for doc in procedures}):
        group = [doc for doc in procedures if (doc.site_id or "") == site]
        latest = max(doc.effective_date for doc in group if doc.effective_date)
        matches = [doc for doc in group if doc.effective_date == latest]
        if len(matches) != 1:
            return historical, "Multiple supplier procedures share the latest effective date."

        active.extend(matches)

    instructions = [
        doc
        for doc in documents
        if doc.audit_id == current.audit_id
        and doc.document_type == "supplier_instruction"
        and doc.site_id in current.scope.sites
        and doc.effective_date
        and doc.effective_date <= current.planned_date
    ]

    return historical + active + instructions, None


class AuditPreparationService:
    def __init__(
        self,
        interpreter: AttentionInterpreter | None = None,
        retriever: EvidenceRetriever | None = None,
        rule_engine: PreparationRuleEngine | None = None,
        validator: AttentionValidator | None = None,
        max_model_calls: int = 8,
    ):
        if max_model_calls < 0:
            raise ValueError("Model call budget cannot be negative")

        self.interpreter = interpreter
        self.retriever = retriever or LexicalEvidenceRetriever()
        self.rule_engine = rule_engine or PreparationRuleEngine()
        self.validator = validator or AttentionValidator()
        self.max_model_calls = max_model_calls

    def prepare(
        self,
        previous_audit: AuditContext | None,
        current_audit: AuditContext,
        documents: list[Document],
        auth: AuthorizationContext,
    ) -> PreparationResult:
        _validate_context(previous_audit, current_audit, auth)
        audit_ids = {current_audit.audit_id}

        if previous_audit:
            audit_ids.add(previous_audit.audit_id)

        visible = [
            doc
            for doc in authorized_documents(documents, auth)
            if doc.organization_id == current_audit.organization_id and doc.audit_id in audit_ids
        ]
        ids = [doc.id for doc in visible]
        if len(ids) != len(set(ids)):
            raise ValueError("Document IDs must be unique across the selected audit context")

        deterministic_items = self.rule_engine.run(previous_audit, current_audit, visible)
        candidates = build_interpretation_candidates(previous_audit, current_audit)
        ai_state = _ai_state(self.interpreter, auth)
        provider = (
            self.interpreter.model.name
            if self.interpreter and ai_state == "enabled"
            else "disabled"
        )
        metadata = ProcessingMetadata(
            provider=provider,
            ai_state=ai_state,
            fingerprint=run_fingerprint(
                previous_audit,
                current_audit,
                visible,
                auth,
                provider=provider,
                max_model_calls=self.max_model_calls,
            ),
            candidate_count=len(candidates),
        )

        attention_items = []
        abstentions = []
        evidence_by_id = {}
        withheld_by_id = {}

        def abstain(candidate, reason, detail, missing=None):
            abstentions.append(
                Abstention(
                    candidate_id=candidate.id,
                    reason=reason,
                    detail=detail,
                    missing_information=missing or [],
                )
            )

        for candidate in candidates:
            selected, temporal_gap = _candidate_documents(
                candidate,
                previous_audit,
                current_audit,
                visible,
            )
            if temporal_gap:
                abstain(candidate, AbstentionReason.TEMPORAL_CONTEXT_UNCLEAR, temporal_gap)
                continue

            unreadable = [doc.id for doc in selected if doc.extraction_status == "unreliable"]
            if unreadable:
                abstain(
                    candidate,
                    AbstentionReason.EXTRACTION_UNRELIABLE,
                    "Source text extraction is unreliable. No interpretation was requested.",
                    [f"Reliable text of {document_id}" for document_id in unreadable],
                )

                continue

            retrieval = self.retriever.retrieve(candidate.query, selected, auth)
            canonical = {chunk.id: chunk for doc in selected for chunk in chunk_document(doc)}
            if any(
                canonical.get(chunk.id) != chunk
                for chunk in [*retrieval.chunks, *retrieval.withheld]
            ):
                abstain(
                    candidate, AbstentionReason.VALIDATION_FAILED, "untrusted_retrieval_evidence"
                )

                continue

            evidence_by_id.update({chunk.id: chunk for chunk in retrieval.chunks})
            withheld_by_id.update({chunk.id: chunk for chunk in retrieval.withheld})

            if not retrieval.complete:
                abstain(
                    candidate,
                    AbstentionReason.RETRIEVAL_INCOMPLETE,
                    "Relevant context exceeded the retrieval budget. The comparison was skipped.",
                )

                continue

            missing = sorted(
                candidate.required_document_types
                - {chunk.document_type for chunk in retrieval.chunks}
            )
            if missing:
                abstain(
                    candidate,
                    AbstentionReason.INSUFFICIENT_EVIDENCE,
                    "Required source material was not found. No interpretation was requested.",
                    missing,
                )

                continue

            if not auth.ai_enabled:
                abstain(
                    candidate,
                    AbstentionReason.TENANT_AI_DISABLED,
                    "AI interpretation is switched off for this tenant. Review evidence.",
                )

                continue

            if self.interpreter is None or metadata.model_calls >= self.max_model_calls:
                abstain(
                    candidate,
                    AbstentionReason.MODEL_UNAVAILABLE,
                    "Interpretation is disabled or its call budget is exhausted. Review evidence.",
                )

                continue

            metadata.model_calls += 1

            try:
                raw = self.interpreter.interpret(candidate, retrieval.chunks, current_audit.scheme)
                response = ModelAttentionResponse.model_validate(raw)
            except ModelProviderError:
                abstain(
                    candidate,
                    AbstentionReason.MODEL_UNAVAILABLE,
                    "Interpretation provider unavailable, refused, or returned incomplete output.",
                )

                continue
            except ValidationError:
                abstain(
                    candidate,
                    AbstentionReason.VALIDATION_FAILED,
                    "Interpretation response did not satisfy the output schema.",
                )

                continue

            validation = self.validator.validate(response, retrieval.chunks, auth, candidate)
            if not validation.valid:
                abstain(candidate, AbstentionReason.VALIDATION_FAILED, ", ".join(validation.errors))
            elif response.result == "abstain":
                abstain(
                    candidate,
                    AbstentionReason.MODEL_ABSTAINED,
                    response.abstention_reason,
                    response.missing_information,
                )
            elif response.result == "no_attention_item":
                metadata.no_attention_count += 1
            else:
                # The validator guarantees a type for attention items (attention_type_required).
                assert response.type is not None
                by_id = {chunk.id: chunk for chunk in retrieval.chunks}
                attention_items.append(
                    AttentionItem(
                        type=response.type,
                        title=f"Review {candidate.id}: {candidate.topic.replace('_', ' ')}",
                        recorded_facts=candidate.recorded_facts,
                        evidence_refs=[
                            evidence_reference(by_id[chunk_id])
                            for chunk_id in response.evidence_chunk_ids
                        ],
                        quotes=[
                            EvidenceQuote(
                                document_id=by_id[quote.chunk_id].document_id,
                                chunk_id=quote.chunk_id,
                                text=quote.text,
                            )
                            for quote in response.quotes
                        ],
                        interpretation=response.interpretation,
                        uncertainty=response.uncertainty,
                        missing_information=response.missing_information,
                        suggested_follow_up=response.suggested_follow_up,
                    )
                )

        evidence = list(evidence_by_id.values())

        return PreparationResult(
            audit_id=current_audit.audit_id,
            deterministic_items=deterministic_items,
            attention_items=attention_items,
            abstentions=abstentions,
            limitations=list(LIMITATIONS),
            evidence=evidence,
            evidence_used=[evidence_reference(chunk) for chunk in evidence],
            withheld_evidence=[evidence_reference(chunk) for chunk in withheld_by_id.values()],
            processing_metadata=metadata,
        )


def prepare_audit(
    previous_audit: AuditContext | None,
    current_audit: AuditContext,
    documents: list[Document],
    auth: AuthorizationContext,
    *,
    interpreter: AttentionInterpreter | None = None,
) -> PreparationResult:
    return AuditPreparationService(interpreter=interpreter).prepare(
        previous_audit,
        current_audit,
        documents,
        auth,
    )
