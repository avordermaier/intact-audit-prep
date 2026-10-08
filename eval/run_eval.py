"""Exact contract checks. Offline fixtures do not measure semantic model quality."""

import argparse
import json
from pathlib import Path

from audit_prep.domain import AuthorizationContext, PreparationResult
from audit_prep.loading import DEFAULT_DATA_DIR
from audit_prep.retrieval import is_authorized
from audit_prep.synthetic import Scenario, build_synthetic_service, load_scenario

CASES_PATH = Path(__file__).with_name("cases.json")


def check_result(
    result: PreparationResult,
    expected: dict,
    auth: AuthorizationContext,
) -> dict[str, bool]:
    actual_facts = [
        [item.type.value, fact.entity_id, fact.value]
        for item in result.deterministic_items
        for fact in item.recorded_facts
    ]

    def sort_key(row):
        return tuple(str(value) for value in row)

    retrieved_ids = {chunk.document_id for chunk in result.evidence}

    attention_ids = {
        ref.document_id for item in result.attention_items for ref in item.evidence_refs
    }

    chunks = {chunk.id: chunk for chunk in result.evidence}
    quotes = [quote for item in result.attention_items for quote in item.quotes]
    serialized = result.model_dump_json().lower()
    checks = {
        "deterministic_signals": sorted(actual_facts, key=sort_key)
        == sorted(expected["deterministic"], key=sort_key),
        "attention_types": [item.type.value for item in result.attention_items]
        == expected["attention_types"],
        "abstentions": [item.reason.value for item in result.abstentions]
        == expected["abstention_reasons"],
        "model_calls": result.processing_metadata.model_calls == expected["model_calls"],
        "no_attention_count": result.processing_metadata.no_attention_count
        == expected.get("no_attention_count", 0),
        "retrieval_sources": set(expected.get("required_retrieved_documents", [])) <= retrieved_ids,
        "attention_sources": set(expected.get("required_attention_documents", [])) <= attention_ids,
        "authorized_evidence": all(is_authorized(chunk, auth) for chunk in result.evidence),
        "valid_references": all(
            ref.chunk_id in chunks and chunks[ref.chunk_id].document_id == ref.document_id
            for item in result.attention_items
            for ref in item.evidence_refs
        ),
        "quotes_verbatim": all(
            quote.chunk_id in chunks
            and " ".join(quote.text.split()) in " ".join(chunks[quote.chunk_id].text.split())
            for quote in quotes
        )
        and all(item.quotes for item in result.attention_items),
        "withheld_evidence": sorted({ref.document_id for ref in result.withheld_evidence})
        == sorted(expected.get("withheld_documents", [])),
        "fingerprint_present": len(result.processing_metadata.fingerprint) == 64,
        "forbidden_claims_absent": all(
            claim.lower() not in serialized for claim in expected.get("forbidden_claims", [])
        ),
        "excluded_text_absent": all(
            text.lower() not in serialized for text in expected.get("excluded_text", [])
        ),
        "counter_evidence_present": all(
            any(text in chunk.text for chunk in result.evidence)
            for text in expected.get("required_evidence_text", [])
        ),
    }

    if "ai_state" in expected:
        checks["ai_state"] = result.processing_metadata.ai_state == expected["ai_state"]

    if expected.get("interpretation_must_be_null"):
        checks["conflict_unresolved"] = all(
            item.interpretation is None for item in result.attention_items
        )

    return checks


def run_evaluation(
    provider: str = "offline",
    data_dir: Path = DEFAULT_DATA_DIR,
    cases_path: Path = CASES_PATH,
) -> dict:
    cases = json.loads(cases_path.read_text("utf-8"))
    results = []
    for case in cases:
        if provider == "azure" and not case.get("live_eligible"):
            continue

        scenario = Scenario(case["scenario"])
        previous, current, documents, auth = load_scenario(scenario, data_dir)
        selected_provider = case.get("provider", provider)
        app = build_synthetic_service(scenario, documents, selected_provider, data_dir)
        result = app.prepare(previous, current, documents, auth)
        checks = check_result(result, case["expected"], auth)
        results.append(
            {
                "case_id": case["id"],
                "passed": all(checks.values()),
                "checks": checks,
                "output": result.model_dump(mode="json"),
            }
        )

    return {
        "mode": provider,
        "semantic_quality_evaluated": False,
        "review_note": (
            "Offline model responses are scripted fixtures. These checks verify system contracts, "
            "not model reasoning. Live interpretation still requires qualitative expert review."
        ),
        "passed": sum(case["passed"] for case in results),
        "total": len(results),
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["offline", "azure"], default="offline")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        report = run_evaluation(args.provider, args.data_dir)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Evaluation configuration error: {error}\n")

    print(f"Audit preparation evaluation ({report['mode']})")

    for case in report["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        print(f"{case['case_id']:<32} {status}")

        if not case["passed"]:
            print(
                "  Failed checks: "
                + ", ".join(key for key, value in case["checks"].items() if not value)
            )

    print(f"\n{report['passed']}/{report['total']} contract cases passed.")
    print(report["review_note"])

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return 0 if report["passed"] == report["total"] and report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
