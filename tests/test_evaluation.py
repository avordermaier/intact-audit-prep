from copy import deepcopy

from audit_prep.domain import PreparationResult
from audit_prep.synthetic import Scenario, build_synthetic_service, load_scenario
from eval.run_eval import check_result, run_evaluation
from tests.conftest import sent_requests


def test_all_offline_contract_cases_pass():
    report = run_evaluation()
    assert report["passed"] == report["total"] == 15
    assert report["semantic_quality_evaluated"] is False


def test_eval_detects_wrong_expected_signal():
    report = run_evaluation()
    case = report["cases"][0]
    result = PreparationResult.model_validate(case["output"])
    auth = load_scenario()[3]

    expected = {
        "deterministic": [],
        "attention_types": [],
        "abstention_reasons": [],
        "model_calls": 100,
    }
    assert not all(check_result(result, expected, auth).values())


def test_cross_tenant_case_matches_baseline_and_sends_no_canary():
    baseline_case = load_scenario(Scenario.NORTHSEA)
    foreign_case = load_scenario(Scenario.CROSS_TENANT)
    baseline_app = build_synthetic_service(Scenario.NORTHSEA, baseline_case[2])
    foreign_app = build_synthetic_service(Scenario.CROSS_TENANT, foreign_case[2])
    assert baseline_app.prepare(*baseline_case) == foreign_app.prepare(*foreign_case)
    assert "BLUECERT" not in sent_requests(foreign_app)[0].model_dump_json()


def test_retrieval_trap_counter_evidence_reaches_interpreter():
    case = load_scenario(Scenario.RETRIEVAL_TRAP)
    app = build_synthetic_service(Scenario.RETRIEVAL_TRAP, case[2])
    result = app.prepare(*case)
    request = sent_requests(app)[0]
    assert any("Subcontracted suppliers follow" in chunk.text for chunk in request.evidence)
    assert result.processing_metadata.no_attention_count == 1


def test_preparation_does_not_mutate_synthetic_inputs():
    case = load_scenario()
    original = deepcopy(case)
    build_synthetic_service(Scenario.NORTHSEA, case[2]).prepare(*case)
    assert case == original


def test_eval_detects_fabricated_quote():
    report = run_evaluation()
    case = next(case for case in report["cases"] if case["case_id"] == "possible-inconsistency")
    result = PreparationResult.model_validate(case["output"])
    result.attention_items[0].quotes[0].text = "Subcontracted suppliers are fully covered."

    expected = {
        "deterministic": [],
        "attention_types": [],
        "abstention_reasons": [],
        "model_calls": 1,
    }
    assert not check_result(result, expected, load_scenario()[3])["quotes_verbatim"]


def test_unreadable_scan_and_disabled_tenant_never_call_the_model():
    expected = {
        Scenario.UNREADABLE_SCAN: "extraction_unreliable",
        Scenario.TENANT_AI_DISABLED: "tenant_ai_disabled",
    }
    for scenario, reason in expected.items():
        case = load_scenario(scenario)
        app = build_synthetic_service(scenario, case[2])
        result = app.prepare(*case)
        assert sent_requests(app) == []
        assert result.abstentions[0].reason == reason
