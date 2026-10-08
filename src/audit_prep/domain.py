"""Explicit contracts for structured facts, evidence, and suggested interpretation."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
AIState = Literal["enabled", "not_configured", "disabled_by_tenant"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SchemeContext(Contract):
    id: Identifier
    name: str
    version: Identifier


class AuditScope(Contract):
    sites: list[Identifier]
    activities: list[Identifier]


class Finding(Contract):
    id: Identifier
    requirement_id: Identifier | None = None
    topic: Identifier
    severity: Literal["minor", "major"] | None = None
    status: Literal["open", "closed"]
    description: str


class CorrectiveAction(Contract):
    id: Identifier
    finding_id: Identifier
    status: Literal["open", "closed"]
    closed_at: date | None = None
    document_id: Identifier | None = None


class ExpectedEvidence(Contract):
    evidence_type: Identifier
    site_id: Identifier | None = None
    required: bool = True


class AuditContext(Contract):
    tenant_id: Identifier
    organization_id: Identifier
    audit_id: Identifier
    audit_type: Literal["surveillance", "initial", "recertification"]
    audit_date: date | None = None
    planned_date: date | None = None
    scheme: SchemeContext
    scope: AuditScope
    findings: list[Finding] = Field(default_factory=list)
    corrective_actions: list[CorrectiveAction] = Field(default_factory=list)
    expected_evidence: list[ExpectedEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_links(self) -> AuditContext:
        finding_ids = [finding.id for finding in self.findings]
        action_ids = [action.id for action in self.corrective_actions]
        if len(finding_ids) != len(set(finding_ids)) or len(action_ids) != len(set(action_ids)):
            raise ValueError("Finding and corrective action IDs must be unique within an audit")

        if any(action.finding_id not in finding_ids for action in self.corrective_actions):
            raise ValueError("Corrective actions must reference a recorded finding")

        if any(
            item.site_id and item.site_id not in self.scope.sites for item in self.expected_evidence
        ):
            raise ValueError("Expected evidence site must be in the audit scope")

        return self


class AuthorizationContext(Contract):
    tenant_id: Identifier
    allowed_audit_ids: set[Identifier]
    allowed_document_ids: set[Identifier] | None = None
    ai_enabled: bool = True


class Document(Contract):
    id: Identifier
    tenant_id: Identifier
    organization_id: Identifier
    audit_id: Identifier
    document_type: Identifier
    site_id: Identifier | None = None
    version: str | None = None
    effective_date: date | None = None
    extraction_status: Literal["ok", "unreliable"] = "ok"
    content: str


class EvidenceChunk(Contract):
    id: Identifier
    document_id: Identifier
    tenant_id: Identifier
    organization_id: Identifier
    audit_id: Identifier
    document_type: Identifier
    text: str
    section: str
    page: int | None = None
    version: str | None = None


class EvidenceReference(Contract):
    document_id: Identifier
    chunk_id: Identifier
    section: str
    page: int | None = None
    version: str | None = None


def evidence_reference(chunk: EvidenceChunk) -> EvidenceReference:
    return EvidenceReference(
        document_id=chunk.document_id,
        chunk_id=chunk.id,
        section=chunk.section,
        page=chunk.page,
        version=chunk.version,
    )


class RecordedFact(Contract):
    fact_type: Identifier
    entity_id: Identifier | None = None
    value: str
    source: Literal["structured_audit_data", "authorized_document_registry"]


class PreparationItemType(StrEnum):
    SCOPE_CHANGE = "scope_change"
    MISSING_EVIDENCE = "missing_evidence"
    EXTRACTION_UNRELIABLE = "extraction_unreliable"


class PreparationItem(Contract):
    type: PreparationItemType
    title: str
    description: str
    recorded_facts: list[RecordedFact]


class AttentionType(StrEnum):
    POSSIBLE_INCONSISTENCY = "possible_inconsistency"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class EvidenceQuote(Contract):
    document_id: Identifier
    chunk_id: Identifier
    text: str


class AttentionItem(Contract):
    type: AttentionType
    title: str
    recorded_facts: list[RecordedFact]
    evidence_refs: list[EvidenceReference]
    quotes: list[EvidenceQuote]
    interpretation: str | None
    uncertainty: list[str]
    missing_information: list[str]
    suggested_follow_up: str | None
    status: Literal["suggested"] = "suggested"


class AbstentionReason(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    RETRIEVAL_INCOMPLETE = "retrieval_incomplete"
    VALIDATION_FAILED = "validation_failed"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_ABSTAINED = "model_abstained"
    TEMPORAL_CONTEXT_UNCLEAR = "temporal_context_unclear"
    EXTRACTION_UNRELIABLE = "extraction_unreliable"
    TENANT_AI_DISABLED = "tenant_ai_disabled"


class Abstention(Contract):
    candidate_id: Identifier
    reason: AbstentionReason
    missing_information: list[str] = Field(default_factory=list)
    detail: str


class InterpretationCandidate(Contract):
    id: Identifier
    candidate_type: Literal["corrective_action_follow_up"] = "corrective_action_follow_up"
    topic: str
    query: str
    recorded_facts: list[RecordedFact]
    corrective_action_document_id: Identifier | None
    required_document_types: set[str] = Field(
        default_factory=lambda: {"corrective_action", "supplier_procedure"}
    )


class InterpretationRequest(Contract):
    candidate_id: Identifier
    candidate_type: str
    topic: str
    recorded_facts: list[RecordedFact]
    evidence: list[EvidenceChunk]
    scheme: SchemeContext


class ModelQuote(Contract):
    chunk_id: str
    text: str


class ModelAttentionResponse(Contract):
    # Every wire field is required. Nullable fields remain explicit in the JSON schema.
    result: Literal["attention_item", "no_attention_item", "abstain"]
    type: AttentionType | None
    evidence_chunk_ids: list[str]
    quotes: list[ModelQuote]
    interpretation: str | None
    uncertainty: list[str]
    missing_information: list[str]
    suggested_follow_up: str | None
    abstention_reason: str | None


class RetrievalResult(Contract):
    chunks: list[EvidenceChunk]
    complete: bool
    detail: str | None = None
    withheld: list[EvidenceChunk] = Field(default_factory=list)


class ProcessingMetadata(Contract):
    provider: str
    ai_state: AIState
    fingerprint: str
    model_calls: int = 0
    candidate_count: int = 0
    no_attention_count: int = 0


class PreparationResult(Contract):
    audit_id: Identifier
    deterministic_items: list[PreparationItem]
    attention_items: list[AttentionItem]
    abstentions: list[Abstention]
    limitations: list[str]
    evidence_used: list[EvidenceReference]
    evidence: list[EvidenceChunk]
    withheld_evidence: list[EvidenceReference]
    processing_metadata: ProcessingMetadata
