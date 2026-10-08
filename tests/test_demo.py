from pathlib import Path

import pytest

from audit_prep.synthetic import Scenario

DEMO_PATH = Path(__file__).resolve().parents[1] / "src" / "audit_prep" / "demo.py"


@pytest.fixture(autouse=True)
def clear_azure_configuration(monkeypatch):
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def app():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(DEMO_PATH), default_timeout=15).run()


def scenario_app(scenario):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    def script(scenario):
        from audit_prep.demo import render_result
        from audit_prep.synthetic import build_synthetic_service, load_scenario

        case = load_scenario(scenario)
        render_result(build_synthetic_service(scenario, case[2]).prepare(*case))

    return AppTest.from_function(script, args=(scenario,), default_timeout=15).run()


def test_workspace_shows_service_results_sources_and_offline_disclosure():
    at = app()
    assert not at.exception
    assert "scripted" in at.info[0].value
    assert "Prepared demo example" in at.info[0].value
    assert not at.button
    settings = next(
        expander for expander in at.sidebar.expander if expander.label == "Demo settings"
    )
    assert not settings.proto.expanded
    assert settings.radio[0].options == ["Scripted demo", "Evidence only"]
    assert settings.toggle[0].label == "AI interpretation for this tenant"
    assert at.selectbox[0].options == [
        "NorthSea audit review",
        "Current procedure missing",
        "Matching procedure",
        "Conflicting review frequencies",
        "Foreign tenant source excluded",
        "Interpretation unavailable",
    ]
    assert [metric.value for metric in at.metric] == ["2", "1", "0"]
    assert any("subcontracted suppliers" in code.value for code in at.code)


def test_missing_and_matching_scenarios_update_results():
    at = app()
    at.selectbox[0].set_value(Scenario.INSUFFICIENT).run()
    assert not at.exception
    assert [metric.value for metric in at.metric] == ["3", "0", "1"]
    assert at.session_state["preparation_result"].abstentions[0].reason == "insufficient_evidence"
    at.selectbox[0].set_value(Scenario.MATCHING).run()
    assert not at.exception
    assert at.success
    assert [metric.value for metric in at.metric] == ["2", "0", "0"]


def test_live_selection_does_not_call_provider_and_drops_stale_result(monkeypatch):
    from audit_prep.model_provider import AzureOpenAIInterpretationModel

    called = []

    def missing_configuration():
        called.append(True)
        raise ValueError("Test Azure configuration missing")

    monkeypatch.setattr(AzureOpenAIInterpretationModel, "from_environment", missing_configuration)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/openai/v1/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "test-deployment")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    at = app()
    at.radio[0].set_value("Live Azure").run()
    assert not at.exception
    assert not called
    assert not at.metric
    assert at.sidebar.button[0].label == "Run live comparison"
    at.button[0].click().run()
    assert called == [True]
    assert not at.exception
    assert at.error[0].value == "Test Azure configuration missing"


@pytest.mark.parametrize(
    "missing", ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_KEY"]
)
def test_partial_azure_configuration_hides_live_mode(monkeypatch, missing):
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_KEY"):
        monkeypatch.setenv(name, "test-value" if name != missing else " ")
    at = app()
    assert not at.exception
    assert at.radio[0].options == ["Scripted demo", "Evidence only"]
    assert "Live interpretation is not configured." in text_of(at)


def test_evidence_only_mode_loads_without_a_button():
    at = app()
    at.radio[0].set_value("Evidence only").run()
    assert not at.exception
    assert not at.button
    assert [metric.value for metric in at.metric] == ["2", "0", "1"]
    assert "evidence only" in at.info[0].value
    assert at.session_state["preparation_result"].processing_metadata.model_calls == 0


def text_of(at):
    elements = [*at.markdown, *at.caption, *at.code, *at.warning, *at.info, *at.success]

    return "\n".join(str(element.value) for element in elements)


def test_label_colors_follow_the_statement_types():
    html = text_of(app())

    for color in ["#DCE1EB", "#D7EBE8", "#F6E3CF", "#E9E4F2", "#E3E6EB"]:
        assert color in html

    for label in ["Recorded", "Document", "AI interpretation", "Uncertain", "Follow-up"]:
        assert label in html


def test_unreadable_scan_is_shown_as_not_interpreted_without_source_text():
    at = scenario_app(Scenario.UNREADABLE_SCAN)
    assert not at.exception
    assert [metric.value for metric in at.metric] == ["3", "0", "1"]
    html = text_of(at)
    assert "Not interpreted" in html
    assert "sc4n qua1ity" not in html


def test_withheld_passage_is_labelled_and_its_text_is_not_displayed():
    at = scenario_app(Scenario.PROMPT_INJECTION)
    assert not at.exception
    assert [metric.value for metric in at.metric] == ["2", "1", "0"]
    html = text_of(at)
    assert "Withheld" in html
    assert "Ignore all previous instructions" not in html


def test_tenant_switch_disables_interpretation_in_the_ui():
    at = app()
    at.toggle[0].set_value(False).run()
    assert not at.exception
    assert [metric.value for metric in at.metric] == ["2", "0", "1"]
    assert "switched off for this tenant" in at.warning[0].value

    assert at.session_state["preparation_result"].processing_metadata.ai_state == (
        "disabled_by_tenant"
    )


def test_document_text_is_escaped_in_html_labels_and_quotes():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    def script():
        from audit_prep.demo import render_result
        from audit_prep.synthetic import Scenario, build_synthetic_service, load_scenario

        case = load_scenario()
        result = build_synthetic_service(Scenario.NORTHSEA, case[2]).prepare(*case)
        result.attention_items[0].quotes[0].text = "<img src=x onerror=alert(1)>"
        render_result(result)

    at = AppTest.from_function(script, default_timeout=15).run()
    assert not at.exception
    html = text_of(at)
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img" not in html
