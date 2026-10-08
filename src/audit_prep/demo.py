"""Streamlit is a presentation adapter. All results come from the application service."""

import os
from html import escape

import streamlit as st

from audit_prep.domain import PreparationResult
from audit_prep.synthetic import Scenario, build_synthetic_service, load_scenario

SCENARIO_LABELS = {
    Scenario.NORTHSEA: "NorthSea audit review",
    Scenario.INSUFFICIENT: "Current procedure missing",
    Scenario.MATCHING: "Matching procedure",
    Scenario.CONFLICTING: "Conflicting review frequencies",
    Scenario.CROSS_TENANT: "Foreign tenant source excluded",
    Scenario.PROVIDER_FAILURE: "Interpretation unavailable",
}

PROVIDERS = {
    "Scripted demo": "offline",
    "Evidence only": "disabled",
    "Live Azure": "azure",
}

# Label and statement colors: recorded facts, documents, AI output, uncertainty, follow-up.
LABEL_STYLES = {
    "recorded": ["Recorded", "#DCE1EB", "#14213D"],
    "document": ["Document", "#D7EBE8", "#0B5A54"],
    "ai": ["AI interpretation", "#F6E3CF", "#8A3F06"],
    "uncertain": ["Uncertain", "#E9E4F2", "#4B3A78"],
    "follow_up": ["Follow-up", "#E3E6EB", "#3B475C"],
    "not_interpreted": ["Not interpreted", "#E3E6EB", "#3B475C"],
    "withheld": ["Withheld", "#F4DAD6", "#8C2B22"],
}

DETERMINISTIC_LABELS = {
    "scope_change": "scope change",
    "missing_evidence": "evidence not found",
    "extraction_unreliable": "document unreadable",
}

AI_STATES = {
    "enabled": "Enabled",
    "not_configured": "Not configured",
    "disabled_by_tenant": "Disabled for this tenant",
}

PAGE_STYLE = """<style>
[class*="st-key-attention-"] { border-color: #B45309; background: #FDFCF8; }
</style>"""


def _label(kind: str, detail: str | None = None) -> None:
    name, background, color = LABEL_STYLES[kind]
    text = f"{name} · {detail}" if detail else name
    st.markdown(
        f'<span style="background:{background};color:{color};padding:4px 12px;'
        "border-radius:8px;font-size:0.8rem;font-weight:600;letter-spacing:0.05em;"
        f'text-transform:uppercase">{escape(text)}</span>',
        unsafe_allow_html=True,
    )


def _quote(text: str) -> None:
    st.markdown(
        '<div style="font-style:italic;color:#3B475C;border-left:3px solid #D7EBE8;'
        f'padding-left:12px;margin-bottom:8px">“{escape(" ".join(text.split()))}”</div>',
        unsafe_allow_html=True,
    )


def _sources(result: PreparationResult, references) -> None:
    chunks = {chunk.id: chunk for chunk in result.evidence}

    for reference in references:
        chunk = chunks[reference.chunk_id]
        st.caption(f"{reference.document_id} · {reference.section}")
        st.code(chunk.text, language=None, wrap_lines=True)


def render_result(result: PreparationResult) -> None:
    st.markdown(PAGE_STYLE, unsafe_allow_html=True)
    metadata = result.processing_metadata
    if metadata.ai_state == "disabled_by_tenant":
        st.warning(
            "AI interpretation is switched off for this tenant. "
            "Signals and source evidence are unchanged. No model was called."
        )

    change_col, attention_col, unavailable_col = st.columns(3)
    change_col.metric("Changes & missing evidence", len(result.deterministic_items))
    attention_col.metric("Suggested reviews", len(result.attention_items))
    unavailable_col.metric("Comparisons unavailable", len(result.abstentions))

    preparation_tab, evidence_tab, context_tab = st.tabs(
        ["Preparation", "Source evidence", "Context"]
    )

    chunks = {chunk.id: chunk for chunk in result.evidence}

    with preparation_tab:
        st.subheader("Changes and missing information")

        for item in result.deterministic_items:
            with st.container(border=True):
                _label("recorded", DETERMINISTIC_LABELS[item.type.value])
                st.markdown(f"**{item.title}**")
                st.write(item.description)

        if not result.deterministic_items:
            st.caption("No deterministic preparation signals in this case.")

        st.subheader("Suggested auditor reviews")

        for index, item in enumerate(result.attention_items):
            with st.container(border=True, key=f"attention-{index}"):
                _label("ai", item.type.value.replace("_", " "))
                st.markdown(f"**{item.title}**")

                if item.interpretation:
                    st.write(item.interpretation)
                else:
                    st.write("The sources conflict. Auditor clarification is required.")

                _label("recorded")

                for fact in item.recorded_facts:
                    st.write(f"{fact.entity_id}: {fact.value}")

                for quote in item.quotes:
                    chunk = chunks[quote.chunk_id]
                    _label("document")
                    version = f" · version {chunk.version}" if chunk.version else ""
                    st.caption(f"{quote.document_id} · {chunk.section}{version}")
                    _quote(quote.text)

                _label("uncertain")

                for text in item.uncertainty:
                    st.write(text)

                if item.missing_information:
                    st.markdown("**Information to clarify**")

                    for text in item.missing_information:
                        st.write(text)

                _label("follow_up")
                st.write(item.suggested_follow_up)

                with st.expander("View cited evidence"):
                    _sources(result, item.evidence_refs)

        if metadata.no_attention_count:
            st.success("The document comparison completed without a suggested review item.")

        for abstention in result.abstentions:
            with st.container(border=True):
                _label("not_interpreted", abstention.reason.value.replace("_", " "))
                st.markdown(f"**{abstention.candidate_id}: review source material**")
                st.write(abstention.detail)

                for text in abstention.missing_information:
                    st.write("Needed: " + text.replace("_", " "))

        for reference in result.withheld_evidence:
            with st.container(border=True):
                _label("withheld", "instruction-like text")
                st.markdown(f"**{reference.document_id}: passage not sent to interpretation**")
                st.write(f"{reference.section}. Review this passage in the original document.")

        st.caption("Suggestions support preparation. Recorded statuses remain unchanged.")

    with evidence_tab:
        st.subheader("Retrieved, authorized source material")

        if not result.evidence:
            st.caption("No source passages are available for this comparison.")

        for document_id in dict.fromkeys(chunk.document_id for chunk in result.evidence):
            references = [ref for ref in result.evidence_used if ref.document_id == document_id]

            with st.expander(document_id, expanded=True):
                _sources(result, references)

    with context_tab:
        st.subheader("Trust and limitations")

        for text in result.limitations:
            st.write(text)

        st.caption(f"Interpretation mode: {metadata.provider}")
        st.caption(f"AI state: {AI_STATES[metadata.ai_state]}")
        st.caption(f"Run fingerprint: {metadata.fingerprint[:16]}")

    st.download_button(
        "Download preparation result",
        result.model_dump_json(indent=2),
        file_name=f"{result.audit_id}-preparation.json",
        mime="application/json",
    )


def main() -> None:
    st.set_page_config(page_title="Audit Preparation Workspace", page_icon="A", layout="wide")

    with st.sidebar:
        st.title("CertAssure Europe")
        st.caption("Synthetic audit workspace")
        scenario = st.selectbox(
            "Review scenario",
            list(SCENARIO_LABELS),
            format_func=lambda option: SCENARIO_LABELS[option],
        )
        azure_configured = all(
            os.environ.get(name, "").strip()
            for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_KEY")
        )
        with st.expander("Demo settings"):
            providers = [
                label for label, value in PROVIDERS.items() if value != "azure" or azure_configured
            ]
            provider_label = st.radio("Processing mode", providers)
            provider = PROVIDERS[provider_label]
            ai_enabled = st.toggle("AI interpretation for this tenant", value=True)
            st.caption("Tenant policy overrides the selected processing mode.")
            if not azure_configured:
                st.caption("Live interpretation is not configured.")
            run_live = False
            if provider == "azure":
                st.caption(
                    "Run a live comparison to send selected synthetic evidence "
                    "to your configured deployment."
                )
                run_live = st.button("Run live comparison")
        st.divider()
        st.caption("Fictional scheme · Sustainable Supply Chain Scheme 3.0")

    st.title("Audit Preparation Workspace")
    st.write("NorthSea Processing Ltd. · Surveillance audit · 18 November 2026")

    if provider == "offline" and ai_enabled:
        st.info("Prepared demo example · scripted interpretation. No external model is called.")
    elif provider == "azure":
        st.info("Live comparison · synthetic audit data.")
    else:
        st.info("Prepared demo example · evidence only. No external model is called.")

    key = [scenario.value, provider, ai_enabled]
    if st.session_state.get("case_key") != key:
        st.session_state.pop("preparation_result", None)

    if run_live or (provider != "azure" and "preparation_result" not in st.session_state):
        try:
            message = (
                "Running live comparison..." if provider == "azure" else "Loading demo example..."
            )
            with st.spinner(message):
                previous, current, documents, auth = load_scenario(scenario)
                auth = auth.model_copy(update={"ai_enabled": auth.ai_enabled and ai_enabled})
                app = build_synthetic_service(scenario, documents, provider)
                st.session_state.preparation_result = app.prepare(
                    previous, current, documents, auth
                )
                st.session_state.case_key = key
        except (ValueError, OSError, PermissionError) as error:
            st.error(str(error))
            return

    result = st.session_state.get("preparation_result")
    if result is None:
        st.caption("Open Demo settings to run a live comparison.")
        return

    render_result(result)


if __name__ == "__main__":
    main()
