from audit_prep.domain import ExpectedEvidence
from audit_prep.loading import load_case
from audit_prep.rules import (
    PreparationRuleEngine,
    detect_missing_evidence,
    detect_scope_changes,
    detect_unreliable_extraction,
)


def test_scope_change_is_exact_and_requires_history():
    previous, current, _ = load_case()
    assert detect_scope_changes(None, current) == []
    items = detect_scope_changes(previous, current)
    assert len(items) == 1
    assert items[0].recorded_facts[0].entity_id == "SITE-B"
    assert items[0].recorded_facts[0].value == "added"
    assert detect_scope_changes(current, current) == []


def test_missing_training_does_not_claim_missing_training_activity():
    _, current, documents = load_case()
    items = detect_missing_evidence(current, documents)
    assert len(items) == 1
    assert items[0].recorded_facts[0].value == "training_record"
    assert "not found" in items[0].title
    assert "untrained" not in items[0].model_dump_json()


def test_other_site_and_old_audit_records_do_not_satisfy_expected_evidence():
    previous, current, documents = load_case()
    record = documents[-1].model_copy(update={"document_type": "training_record"})
    assert detect_missing_evidence(current, [*documents, record]) == []

    for update in [{"site_id": "SITE-A"}, {"audit_id": previous.audit_id}, {"content": " "}]:
        assert (
            len(detect_missing_evidence(current, [*documents, record.model_copy(update=update)]))
            == 1
        )


def test_optional_evidence_and_scope_removals():
    previous, current, documents = load_case()
    current = current.model_copy(
        update={"expected_evidence": [ExpectedEvidence(evidence_type="optional", required=False)]}
    )
    assert detect_missing_evidence(current, documents) == []
    items = detect_scope_changes(current, previous)
    assert items[0].recorded_facts[0].value == "removed"


def test_unreliable_extraction_is_flagged_without_judging_content():
    previous, current, documents = load_case()
    assert detect_unreliable_extraction(documents) == []
    scan = documents[1].model_copy(update={"extraction_status": "unreliable"})
    items = detect_unreliable_extraction([documents[0], scan])
    assert len(items) == 1
    assert items[0].recorded_facts[0].entity_id == scan.id
    assert items[0].recorded_facts[0].value == "unreliable"
    engine_items = PreparationRuleEngine().run(previous, current, [documents[0], scan])
    assert items[0] in engine_items
