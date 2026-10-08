"""Load the synthetic case. Keep filesystem paths outside domain and retrieval."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from audit_prep.domain import AuditContext, Contract, Document

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "northsea"


class DocumentMetadata(Contract):
    id: str
    tenant_id: str
    organization_id: str
    audit_id: str
    document_type: str
    site_id: str | None = None
    version: str | None = None
    effective_date: str | None = None
    extraction_status: Literal["ok", "unreliable"] = "ok"
    path: str = Field(min_length=1)


def load_case(
    data_dir: Path = DEFAULT_DATA_DIR,
) -> tuple[AuditContext, AuditContext, list[Document]]:
    root = data_dir.resolve()
    previous = AuditContext.model_validate_json((root / "audit_previous.json").read_text("utf-8"))
    current = AuditContext.model_validate_json((root / "audit_current.json").read_text("utf-8"))
    metadata = json.loads((root / "documents.json").read_text("utf-8"))
    documents = []
    document_root = (root / "documents").resolve()

    for raw in metadata:
        item = DocumentMetadata.model_validate(raw)
        path = (document_root / item.path).resolve()
        if not path.is_relative_to(document_root) or not path.is_file():
            raise ValueError("Document path must resolve to a file inside the documents directory")

        documents.append(
            Document.model_validate(
                {**item.model_dump(exclude={"path"}), "content": path.read_text("utf-8")}
            )
        )

    identities = [(doc.tenant_id, doc.audit_id, doc.id) for doc in documents]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate document identity in the registry")

    return previous, current, documents
