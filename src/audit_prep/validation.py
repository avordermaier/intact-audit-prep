"""Reject invalid references and overreaching suggestions before product output."""

import re

from audit_prep.domain import (
    AttentionType,
    AuthorizationContext,
    Contract,
    EvidenceChunk,
    InterpretationCandidate,
    ModelAttentionResponse,
)
from audit_prep.retrieval import is_authorized, looks_like_injection

# A conservative English guardrail, not a semantic entailment or safety proof.
PROHIBITED_CLAIMS = re.compile(
    r"non[ -]?complian\w*|certification.{0,30}(?:risk|revoke|withdraw)|"
    r"(?:corrective action|CA-\d+).{0,35}(?:ineffective|failed)|"
    r"(?:ineffective|failed).{0,35}corrective action|re[- ]?open|"
    r"(?:create|issue|raise).{0,30}(?:finding|nonconformity)|"
    r"untrained|(?:training|requirements?).{0,25}(?:violated|breached)",
    re.IGNORECASE,
)


MAX_QUOTE_LENGTH = 500


def _normalized(text: str) -> str:
    return " ".join(text.split())


class ValidationResult(Contract):
    valid: bool
    errors: list[str]


class AttentionValidator:
    def validate(
        self,
        response: ModelAttentionResponse,
        evidence: list[EvidenceChunk],
        auth: AuthorizationContext,
        candidate: InterpretationCandidate,
    ) -> ValidationResult:
        errors = []
        by_id = {chunk.id: chunk for chunk in evidence}
        ids = response.evidence_chunk_ids
        if len(ids) != len(set(ids)):
            errors.append("duplicate_evidence_reference")

        if any(chunk_id not in by_id for chunk_id in ids):
            errors.append("unknown_evidence_reference")

        if any(not is_authorized(chunk, auth) for chunk in evidence):
            errors.append("unauthorized_evidence")

        selected = [by_id[chunk_id] for chunk_id in ids if chunk_id in by_id]

        texts = [
            response.interpretation or "",
            response.suggested_follow_up or "",
            response.abstention_reason or "",
            *response.uncertainty,
            *response.missing_information,
        ]
        if any(PROHIBITED_CLAIMS.search(text) for text in texts):
            errors.append("prohibited_judgment")

        if any(looks_like_injection(text) for text in texts):
            errors.append("echoed_instruction")

        if any(len(text) > 2000 for text in texts) or sum(map(len, texts)) > 6000:
            errors.append("response_text_limit")

        quoted = [
            by_id.get(quote.chunk_id) if quote.chunk_id in ids else None
            for quote in response.quotes
        ]

        if any(len(quote.text) > MAX_QUOTE_LENGTH for quote in response.quotes):
            errors.append("quote_too_long")

        if any(chunk is None for chunk in quoted):
            errors.append("quote_not_in_cited_evidence")
        elif any(
            not _normalized(quote.text) or _normalized(quote.text) not in _normalized(chunk.text)
            for quote, chunk in zip(response.quotes, quoted, strict=True)
            if chunk
        ):
            errors.append("quote_not_verbatim")

        if response.result in {"attention_item", "no_attention_item"}:
            if not candidate.required_document_types <= {
                chunk.document_type for chunk in quoted if chunk
            }:
                errors.append("required_quotes_missing")

            if len({chunk.document_id for chunk in selected}) < 2:
                errors.append("two_distinct_documents_required")

            if not candidate.required_document_types <= {chunk.document_type for chunk in selected}:
                errors.append("required_evidence_types_not_cited")

            if candidate.corrective_action_document_id not in {
                chunk.document_id for chunk in selected
            }:
                errors.append("corrective_action_source_not_cited")

            if response.abstention_reason is not None:
                errors.append("unexpected_abstention_reason")

        if response.result == "attention_item":
            if response.type is None:
                errors.append("attention_type_required")

            if not any(text.strip() for text in response.uncertainty):
                errors.append("uncertainty_required")

            if not response.suggested_follow_up or not response.suggested_follow_up.strip():
                errors.append("follow_up_required")

            if response.type == AttentionType.POSSIBLE_INCONSISTENCY:
                if not response.interpretation or not response.interpretation.strip():
                    errors.append("interpretation_required")

            if response.type == AttentionType.CONFLICTING_EVIDENCE:
                if response.interpretation is not None:
                    errors.append("conflict_must_remain_unresolved")
        else:
            if response.type is not None or response.interpretation is not None:
                errors.append("unexpected_attention_fields")

            if response.suggested_follow_up is not None:
                errors.append("unexpected_follow_up")

            if response.result == "no_attention_item":
                if response.uncertainty or response.missing_information:
                    errors.append("ambiguous_no_attention_result")
            elif (
                not response.abstention_reason
                or not response.abstention_reason.strip()
                or not any(text.strip() for text in response.missing_information)
            ):
                errors.append("abstention_explanation_required")

        if response.result == "abstain" and response.quotes:
            errors.append("unexpected_quotes")

        return ValidationResult(valid=not errors, errors=errors)
