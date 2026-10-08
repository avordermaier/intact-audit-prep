"""Exact changes and missing metadata matches. No model judgment."""

from audit_prep.domain import (
    AuditContext,
    Document,
    PreparationItem,
    PreparationItemType,
    RecordedFact,
)


def detect_scope_changes(
    previous: AuditContext | None, current: AuditContext
) -> list[PreparationItem]:
    if previous is None:
        return []

    items = []
    for label, old, new in [
        ["Site", previous.scope.sites, current.scope.sites],
        ["Activity", previous.scope.activities, current.scope.activities],
    ]:
        for change, values in [
            ["added", set(new) - set(old)],
            ["removed", set(old) - set(new)],
        ]:
            for value in sorted(values):
                items.append(
                    PreparationItem(
                        type=PreparationItemType.SCOPE_CHANGE,
                        title=f"{label} {value} {change}",
                        description=f"{value} was {change} since audit {previous.audit_id}.",
                        recorded_facts=[
                            RecordedFact(
                                fact_type=f"scope_{label.lower()}_change",
                                entity_id=value,
                                value=change,
                                source="structured_audit_data",
                            )
                        ],
                    )
                )

    return items


def detect_missing_evidence(
    current: AuditContext, documents: list[Document]
) -> list[PreparationItem]:
    items = []
    for expected in current.expected_evidence:
        if not expected.required:
            continue

        found = any(
            doc.audit_id == current.audit_id
            and doc.tenant_id == current.tenant_id
            and doc.organization_id == current.organization_id
            and doc.document_type == expected.evidence_type
            and doc.site_id == expected.site_id
            and bool(doc.content.strip())
            and (
                doc.effective_date is None
                or current.planned_date is None
                or doc.effective_date <= current.planned_date
            )
            for doc in documents
        )
        if found:
            continue

        location = f" for {expected.site_id}" if expected.site_id else ""
        label = expected.evidence_type.replace("_", " ")
        items.append(
            PreparationItem(
                type=PreparationItemType.MISSING_EVIDENCE,
                title=f"Expected {label}{location} not found",
                description=(
                    f"Expected {label}{location} was not found in the available, authorized "
                    "audit material. This does not establish whether the activity occurred."
                ),
                recorded_facts=[
                    RecordedFact(
                        fact_type="expected_evidence_not_found",
                        entity_id=expected.site_id,
                        value=expected.evidence_type,
                        source="authorized_document_registry",
                    )
                ],
            )
        )

    return items


def detect_unreliable_extraction(documents: list[Document]) -> list[PreparationItem]:
    return [
        PreparationItem(
            type=PreparationItemType.EXTRACTION_UNRELIABLE,
            title=f"Document {doc.id} could not be read reliably",
            description=(
                f"The extracted text of {doc.id} is unreliable. Review the original document. "
                "No automated comparison relies on it."
            ),
            recorded_facts=[
                RecordedFact(
                    fact_type="document_extraction_status",
                    entity_id=doc.id,
                    value=doc.extraction_status,
                    source="authorized_document_registry",
                )
            ],
        )
        for doc in sorted(documents, key=lambda doc: doc.id)
        if doc.extraction_status == "unreliable"
    ]


class PreparationRuleEngine:
    def run(
        self, previous: AuditContext | None, current: AuditContext, documents: list[Document]
    ) -> list[PreparationItem]:
        """The application must pass only authorized documents."""
        return (
            detect_scope_changes(previous, current)
            + detect_missing_evidence(current, documents)
            + detect_unreliable_extraction(documents)
        )
