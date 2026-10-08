"""One synthetic dataset for CLI, evaluation, and UI. Offline interpretation is scripted."""

import json
from datetime import date
from enum import StrEnum
from pathlib import Path

from audit_prep.domain import AuditContext, AuthorizationContext, Document, ModelAttentionResponse
from audit_prep.interpretation import AttentionInterpreter
from audit_prep.loading import DEFAULT_DATA_DIR, load_case
from audit_prep.model_provider import AzureOpenAIInterpretationModel, FakeInterpretationModel
from audit_prep.preparation import AuditPreparationService
from audit_prep.retrieval import LexicalEvidenceRetriever, chunk_document, looks_like_injection


class Scenario(StrEnum):
    NORTHSEA = "northsea"
    INSUFFICIENT = "insufficient-evidence"
    MATCHING = "matching"
    CONFLICTING = "conflicting-evidence"
    RETRIEVAL_TRAP = "retrieval-trap"
    CROSS_TENANT = "cross-tenant"
    INVALID_REFERENCE = "invalid-reference"
    MODEL_ABSTAINED = "model-abstained"
    PROVIDER_FAILURE = "provider-failure"
    RETRIEVAL_BUDGET = "retrieval-budget"
    TEMPORAL_GAP = "temporal-gap"
    PROMPT_INJECTION = "prompt-injection"
    UNREADABLE_SCAN = "unreadable-scan"
    TENANT_AI_DISABLED = "tenant-ai-disabled"


def load_scenario(
    scenario: Scenario = Scenario.NORTHSEA,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> tuple[AuditContext, AuditContext, list[Document], AuthorizationContext]:
    previous, current, documents = load_case(data_dir)
    variant_dir = data_dir / "variants"

    if scenario == Scenario.INSUFFICIENT:
        documents = [doc for doc in documents if doc.document_type != "supplier_procedure"]
    elif scenario in {Scenario.MATCHING, Scenario.RETRIEVAL_TRAP}:
        matching = scenario == Scenario.MATCHING
        replacement = (
            "supplier_procedure_v5_matching.txt"
            if matching
            else "supplier_procedure_retrieval_trap.txt"
        )
        documents[1] = documents[1].model_copy(
            update={
                "id": "DOC-SUPPLIER-PROC-V5" if matching else "DOC-SUPPLIER-PROC-TRAP",
                "version": "5" if matching else "4",
                "effective_date": date(2026, 9, 1) if matching else date(2026, 8, 1),
                "content": (variant_dir / replacement).read_text("utf-8"),
            }
        )
    elif scenario == Scenario.CONFLICTING:
        documents.append(
            Document(
                id="DOC-SITE-B-INSTRUCTION",
                tenant_id=current.tenant_id,
                organization_id=current.organization_id,
                audit_id=current.audit_id,
                document_type="supplier_instruction",
                site_id="SITE-B",
                effective_date=date(2026, 8, 1),
                content=(variant_dir / "site_b_instruction.txt").read_text("utf-8"),
            )
        )
    elif scenario == Scenario.CROSS_TENANT:
        documents.append(
            documents[1].model_copy(
                update={
                    "id": "DOC-BLUECERT-SECRET",
                    "tenant_id": "tenant-bluecert",
                    "content": (variant_dir / "secret_supplier_procedure.txt").read_text("utf-8"),
                }
            )
        )
    elif scenario == Scenario.TEMPORAL_GAP:
        documents[1] = documents[1].model_copy(update={"version": None})
    elif scenario == Scenario.PROMPT_INJECTION:
        documents[1] = documents[1].model_copy(
            update={
                "id": "DOC-SUPPLIER-PROC-INJECTION",
                "content": (variant_dir / "supplier_procedure_injection.txt").read_text("utf-8"),
            }
        )
    elif scenario == Scenario.UNREADABLE_SCAN:
        documents[1] = documents[1].model_copy(
            update={
                "id": "DOC-SUPPLIER-PROC-SCAN",
                "extraction_status": "unreliable",
                "content": (variant_dir / "supplier_procedure_unreadable_scan.txt").read_text(
                    "utf-8"
                ),
            }
        )

    auth = AuthorizationContext(
        tenant_id=current.tenant_id,
        allowed_audit_ids={previous.audit_id, current.audit_id},
        ai_enabled=scenario != Scenario.TENANT_AI_DISABLED,
    )

    return previous, current, documents, auth


def _quote_chunk_id(documents: list[Document], document_id: str, text: str) -> str:
    needle = " ".join(text.split())

    for doc in documents:
        for chunk in chunk_document(doc) if doc.id == document_id else []:
            if needle in " ".join(chunk.text.split()):
                return chunk.id

    raise ValueError(f"Offline fixture quote not found in {document_id}")


def build_synthetic_service(
    scenario: Scenario,
    documents: list[Document],
    provider: str = "offline",
    data_dir: Path = DEFAULT_DATA_DIR,
) -> AuditPreparationService:
    if provider == "disabled":
        interpreter = None
    elif provider == "azure":
        interpreter = AttentionInterpreter(AzureOpenAIInterpretationModel.from_environment())
    elif provider == "offline":
        fixtures = json.loads((data_dir / "offline_responses.json").read_text("utf-8"))
        fixture_key = scenario.value if scenario.value in fixtures else Scenario.NORTHSEA.value
        raw = fixtures[fixture_key].copy()
        document_ids = raw.pop("evidence_document_ids")
        extra_ids = raw.pop("extra_chunk_ids", [])
        quotes = raw.pop("quotes")

        raw["evidence_chunk_ids"] = [
            chunk.id
            for doc in documents
            if doc.id in document_ids
            for chunk in chunk_document(doc)
            if not chunk.text.startswith(("Document ID:", "Section "))
            and not looks_like_injection(chunk.text)
        ] + extra_ids
        raw["quotes"] = [
            {
                "chunk_id": _quote_chunk_id(documents, quote["document_id"], quote["text"]),
                "text": quote["text"],
            }
            for quote in quotes
            if any(doc.id == quote["document_id"] for doc in documents)
        ]
        responses = (
            {}
            if scenario == Scenario.PROVIDER_FAILURE
            else {"CA-214": ModelAttentionResponse.model_validate(raw)}
        )
        interpreter = AttentionInterpreter(FakeInterpretationModel(responses))
    else:
        raise ValueError("Provider must be offline, disabled, or azure")

    retriever = LexicalEvidenceRetriever(
        max_chunks=2 if scenario == Scenario.RETRIEVAL_BUDGET else 24
    )

    return AuditPreparationService(interpreter=interpreter, retriever=retriever)
