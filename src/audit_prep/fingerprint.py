"""Identify the inputs and AI settings of one preparation run."""

import hashlib
import json

from audit_prep.domain import AuditContext, AuthorizationContext, Document
from audit_prep.model_provider import SYSTEM_PROMPT


def run_fingerprint(
    previous: AuditContext | None,
    current: AuditContext,
    documents: list[Document],
    auth: AuthorizationContext,
    *,
    provider: str,
    max_model_calls: int,
) -> str:
    """Hash authorized inputs only, so unauthorized documents cannot change the result."""
    payload = {
        "previous": previous.model_dump(mode="json") if previous else None,
        "current": current.model_dump(mode="json"),
        "documents": [doc.model_dump(mode="json") for doc in sorted(documents, key=_document_id)],
        "authorization": {
            "tenant_id": auth.tenant_id,
            "audit_ids": sorted(auth.allowed_audit_ids),
            "document_ids": sorted(auth.allowed_document_ids)
            if auth.allowed_document_ids is not None
            else None,
            "ai_enabled": auth.ai_enabled,
        },
        "provider": provider,
        "prompt": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "max_model_calls": max_model_calls,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    return hashlib.sha256(encoded.encode()).hexdigest()


def _document_id(document: Document) -> str:
    return document.id
