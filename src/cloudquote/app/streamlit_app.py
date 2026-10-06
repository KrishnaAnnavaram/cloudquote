"""Streamlit UI: requirement text -> editable validated spec -> side-by-side priced quotes.

Every control on the page changes the result; there are no decorative options.
Run with ``cloudquote ui`` or ``streamlit run src/cloudquote/app/streamlit_app.py``.
"""
from __future__ import annotations

import json

import streamlit as st
from pydantic import ValidationError

from cloudquote.config import Settings, load_dotenv
from cloudquote.explain import explain, explain_with_llm
from cloudquote.extract import ExtractionError
from cloudquote.service import QuoteService
from cloudquote.units import fmt_money, money

EXAMPLE = ("Production API: 2 instances with 4 vCPUs and 16 GiB RAM each, running 24/7, plus 2 TB of "
           "frequently accessed object storage and 500 GB of egress to the internet per month, for 12 months.")


@st.cache_resource
def get_service() -> tuple[QuoteService, Settings]:
    load_dotenv()
    settings = Settings.from_env()
    return QuoteService(settings.catalog(), settings.extractor_impl()), settings


def show_estimate(est, settings: Settings) -> None:
    cols = st.columns(len(est.quotes))
    for col, (provider, q) in zip(cols, est.quotes.items()):
        with col:
            badge = " (cheapest)" if est.cheapest == provider else ""
            st.subheader(f"{provider.upper()} - {q.region}{badge}")
            st.metric("Monthly total", fmt_money(q.monthly_total))
            st.metric(f"Total for {money(q.term_months)} months", fmt_money(q.term_total))
            st.dataframe(
                [{"item": line.description, "quantity": f"{line.quantity.normalize():f} {line.quantity_unit}",
                  "rate": f"{line.rate} {line.rate_unit}", "per month": fmt_money(line.monthly),
                  "term": fmt_money(line.amount)} for line in q.lines],
                hide_index=True,
            )
            for w in q.warnings:
                st.warning(w)
            for u in q.unavailable:
                st.error(f"Not priced: {u}")
    llm = settings.llm() if settings.explain_with_llm else None
    st.info(explain_with_llm(est, llm)[0] if llm else explain(est))
    st.download_button("Download quote (JSON)", json.dumps(est.as_dict(), indent=2), "quote.json",
                       "application/json")


def main() -> None:
    st.set_page_config(page_title="cloudquote", layout="wide")
    service, settings = get_service()
    cat = service.catalog
    st.title("cloudquote")
    if cat.is_sample:
        st.warning(f"{cat.disclaimer} (catalog {cat.catalog_version}, as of {cat.as_of})")
    else:
        st.caption(f"Catalog {cat.catalog_version}, prices as of {cat.as_of}")

    text = st.text_area("Describe what you need", value=EXAMPLE, height=110)
    if st.button("Extract requirement", type="primary"):
        try:
            extraction = service.extractor.extract(text)
        except ExtractionError as exc:
            st.error(str(exc))
        else:
            st.session_state["spec_json"] = extraction.requirement.model_dump_json(indent=2)
            st.session_state["assumptions"] = list(extraction.assumptions)

    if "spec_json" in st.session_state:
        for note in st.session_state.get("assumptions", []):
            st.caption(f"Assumption: {note}")
        spec_json = st.text_area("Structured requirement (edit to adjust; validated before pricing)",
                                 st.session_state["spec_json"], height=320)
        try:
            est = service.from_requirement(spec_json)
        except (ValidationError, ValueError) as exc:
            st.error(f"Invalid requirement: {exc}")
            return
        show_estimate(est, settings)


main()
