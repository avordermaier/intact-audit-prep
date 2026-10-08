# Technical notes

## Demo interaction

- The workspace opens into a prepared example. Changing the scenario loads its example immediately.
- Scripted examples use predefined interpretation responses, with rules, retrieval, and validation
  computed locally. No background preparation worker is implemented.
- Processing mode and tenant AI policy are in the collapsed **Demo settings** panel. Tenant policy
  disables interpretation while preserving deterministic signals and available evidence.
- **Live Azure** appears only when endpoint, deployment, and API key environment variables are
  nonempty. This checks configuration presence, not deployment availability or credential validity.
- Selecting live mode clears the previous example without sending evidence. **Run live comparison**
  in Demo settings explicitly starts live processing.

## Authorization and evidence

- `AuthorizationContext` is mandatory. Tenant, audit, and optional document grants apply before
  rule evaluation, tokenization, ranking, and provider serialization. An empty document allowlist
  denies all documents; `None` permits documents within the tenant and audit grants.
- The service also narrows documents to the selected organization and current/previous audit pair.
  Authentication is outside this prototype; a production caller must derive grants from identity.
- The fixture loader rejects paths escaping the document directory and duplicate registry identities.
  Retrieved chunks are checked against canonical source content before interpretation.
- Procedure selection uses the latest eligible effective date per site. Missing version/date,
  ambiguous latest sources, changed scheme version, and unsuitable chronology disable comparison.
- Extraction status is supplied metadata. Unreliable selected sources stop comparison before
  retrieval. The prototype does not perform OCR or measure extraction quality.
- Retrieval expands selected documents to all paragraphs within budget, including later
  counter-evidence. This does not prove recall across unselected documents or vocabulary variants.
- Recognized instruction-like passages are withheld and their source locations remain available
  for manual review. Screening is an English heuristic, not a complete prompt-injection defense.

## Output contract

- Recorded facts remain separate from generated interpretation. No provider can mutate audit state.
- Both positive and negative comparisons require two distinct documents, the exact corrective-action
  source, both required document types, and verbatim quotes for those types.
- The provider returns chunk IDs. The application constructs trusted document/section/version
  references. Unknown or duplicate citations and quotes outside cited passages are rejected.
- Quote matching normalizes whitespace; paraphrases and fabricated wording fail. This checks source
  text, not semantic entailment. An interpretation with valid quotes can still be wrong.
- Possible inconsistency requires interpretation, uncertainty, and follow-up. Conflicting evidence
  keeps interpretation null. Abstention requires an explanation and a named information gap.
- The English judgment guard rejects several prohibited claims and echoed instructions. It is
  incomplete and can reject negated prohibited wording. Every suggestion requires auditor review.
- `evidence` contains retrieved passages, including fallback context; item `evidence_refs` contains
  validated citations. Withheld text is omitted from the product output.
- A no-attention response is validated, but the result retains its count and retrieved evidence,
  not a separate negative-comparison record. It is not an audit-wide all-clear.

## Budgets and provenance

- Retrieval defaults to 24 chunks and 16,000 source characters; model context is limited to 32,000
  serialized characters. Incomplete retrieval skips interpretation.
- At most eight interpretation attempts are allowed per run. The Azure adapter uses a 20-second
  timeout, no automatic retries, and a bounded completion. Raw SDK failures are not exposed.
- `model_calls` counts attempts, including scripted ones, rather than confirmed external requests.
- Tenant AI disable keeps signals and available evidence while preventing interpretation.
- The SHA-256 run fingerprint covers authorized inputs, grants, provider name, prompt, AI setting,
  and call budget. It does not include model deployment identity, retrieval settings, response
  recipes, or generated output. It is an input identifier, not a complete reproduction manifest.
- Live requests use `store=False`; this alone does not establish every provider retention or
  monitoring policy. Validate the actual deployment and customer obligations separately.

## Supporting regressions

The UI exposes six representative walkthroughs. All 14 scenarios remain accessible through the
CLI; the evaluator runs 15 contracts because it checks the baseline with and without interpretation.

| Additional case | Contract |
| --- | --- |
| Later counter-evidence | Section 8 reaches interpretation and avoids an unnecessary review |
| Fabricated reference | Reject an unknown chunk while preserving source evidence |
| Model abstention | Preserve the explained gap even when both document types are available |
| Retrieval budget | Stop interpretation on knowingly incomplete context |
| Temporal gap | Stop comparison when procedure version is unknown |
| Source injection | Withhold recognized instructions while retaining usable passages |
| Unreadable scan | Flag unreliable extraction and make no interpretation request |
| Tenant AI disabled | Preserve deterministic signals and evidence without a model call |

The tests additionally cover allowlists, foreign organizations, schema errors, quote fabrication,
path traversal, input immutability, fingerprints, SDK serialization through a local HTTP transport,
and Streamlit rendering. These checks verify contracts, not auditor outcomes or live-model quality.

For installations outside the checkout, pass `--data-dir` pointing to `data/northsea`.
Without uv, create a virtual environment and install `.[ai,demo,dev]`; this resolves dependency
ranges rather than the pinned uv lockfile.
