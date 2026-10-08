"""Command-line adapter for the same preparation service used by the demo."""

import argparse
from pathlib import Path

from audit_prep.loading import DEFAULT_DATA_DIR
from audit_prep.synthetic import Scenario, build_synthetic_service, load_scenario


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the synthetic NorthSea audit")
    parser.add_argument(
        "--scenario", choices=list(Scenario), type=Scenario, default=Scenario.NORTHSEA
    )
    parser.add_argument("--provider", choices=["offline", "disabled", "azure"], default="offline")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--json", action="store_true", help="Print the complete result as JSON")
    parser.add_argument("--output", type=Path, help="Save the complete result as JSON")
    args = parser.parse_args()

    try:
        case = load_scenario(args.scenario, args.data_dir)
        app = build_synthetic_service(args.scenario, case[2], args.provider, args.data_dir)
        result = app.prepare(*case)
    except (ValueError, OSError, PermissionError) as error:
        parser.exit(2, f"Preparation configuration error: {error}\n")

    serialized = result.model_dump_json(indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")

    if args.json:
        print(serialized)
        return 0

    print(f"Audit preparation: {result.audit_id}")
    print(f"Interpretation: {result.processing_metadata.provider}")
    print(f"Run fingerprint: {result.processing_metadata.fingerprint[:16]}")

    if args.provider == "offline":
        print("Scripted offline interpretation. No external model was called.")

    for item in result.deterministic_items:
        print(f"[{item.type.value}] {item.title}")

    for item in result.attention_items:
        print(f"[{item.type.value}] {item.title}")

        if item.interpretation:
            print("  " + item.interpretation)

        for uncertainty in item.uncertainty:
            print("  Uncertainty: " + uncertainty)

        print(f"  Follow-up: {item.suggested_follow_up}")

        for quote in item.quotes:
            print(f'  Quote ({quote.document_id}): "{quote.text}"')

        print("  Sources: " + ", ".join(sorted({ref.document_id for ref in item.evidence_refs})))

    for abstention in result.abstentions:
        print(f"[abstain: {abstention.reason.value}] {abstention.detail}")

    for reference in result.withheld_evidence:
        print(f"[withheld] {reference.document_id}: {reference.section} (instruction-like text)")

    if result.processing_metadata.no_attention_count:
        print("Comparison completed without a suggested attention item.")

    return 0
