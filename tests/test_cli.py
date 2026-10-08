import json

import pytest

from audit_prep.cli import main


def test_cli_outputs_json_and_saves_the_same_result(monkeypatch, capsys, tmp_path):
    output = tmp_path / "preparation.json"
    monkeypatch.setattr("sys.argv", ["audit-prep", "--json", "--output", str(output)])
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result == json.loads(output.read_text("utf-8"))
    assert result["processing_metadata"]["provider"] == "offline_fixture"
    assert len(result["attention_items"]) == 1


def test_cli_discloses_scripted_model_and_missing_source(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["audit-prep", "--scenario", "insufficient-evidence"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "No external model was called" in output
    assert "abstain: insufficient_evidence" in output


def test_cli_missing_azure_configuration_returns_actionable_error(monkeypatch, capsys):
    for name in ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr("sys.argv", ["audit-prep", "--provider", "azure"])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2
    assert "AZURE_OPENAI_ENDPOINT" in capsys.readouterr().err


def test_cli_reports_quotes_withheld_passages_and_fingerprint(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["audit-prep", "--scenario", "prompt-injection"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "Quote (DOC-CA214)" in output
    assert "[withheld] DOC-SUPPLIER-PROC-INJECTION" in output
    assert "Run fingerprint:" in output
    assert "Ignore all previous instructions" not in output


def test_cli_reports_unreadable_scan_and_disabled_tenant(monkeypatch, capsys):
    for scenario, reason in [
        ["unreadable-scan", "abstain: extraction_unreliable"],
        ["tenant-ai-disabled", "abstain: tenant_ai_disabled"],
    ]:
        monkeypatch.setattr("sys.argv", ["audit-prep", "--scenario", scenario])
        assert main() == 0
        assert reason in capsys.readouterr().out
