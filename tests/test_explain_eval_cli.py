"""Explanations that cannot contradict the numbers, the evaluation harness (problem 6) and the CLI."""
import json
from decimal import Decimal

import pytest

from cloudquote.cli import main
from cloudquote.config import Settings
from cloudquote.cost import estimate
from cloudquote.evaluate import evaluate, load_eval_set
from cloudquote.explain import explain, explain_with_llm, numbers_consistent
from cloudquote.extract import RuleBasedExtractor
from cloudquote.llm import ScriptedLLM, build_llm
from cloudquote.service import QuoteService
from cloudquote.spec import ComputeSpec, Requirement, StorageSpec


@pytest.fixture
def est(catalog):
    return estimate(catalog, Requirement(compute=ComputeSpec(vcpus=2, memory_gib=8),
                                         storage=StorageSpec(size_gb=1024)))


def test_deterministic_explanation_has_totals_and_disclaimer(est):
    text = explain(est)
    assert "$93.63 per month" in text          # 730 x 0.096 + 1024 x 0.023
    assert "$69.39 per month" in text          # 730 x 0.067 + 1024 x 0.020
    assert "Cheapest complete quote: GCP" in text and "SAMPLE PRICES" in text


def test_llm_explanation_with_invented_numbers_is_discarded(est):
    text, used = explain_with_llm(est, ScriptedLLM(["AWS costs about $90 and GCP $70."]))
    assert not used and text == explain(est)
    good = "GCP is cheaper at $69.39 a month versus $93.63 on AWS."
    assert numbers_consistent(good, est)
    assert explain_with_llm(est, ScriptedLLM([good])) == (good, True)


def test_eval_harness_scores_core_items_perfectly(catalog):
    rows = [r for r in load_eval_set() if r.get("split") != "hard"]
    report = evaluate(rows, RuleBasedExtractor(), catalog)
    assert report.n >= 12 and report.extraction_failures == 0
    assert set(report.field_accuracy.values()) == {1.0}
    assert report.sku_accuracy == 1.0
    assert report.cost_max_abs_error_usd < 0.001


def test_eval_harness_reports_hard_misses_honestly(catalog):
    rows = [r for r in load_eval_set() if r.get("split") == "hard"]
    report = evaluate(rows, RuleBasedExtractor(), catalog)
    assert report.extraction_failures >= 1 and report.sku_accuracy < 1.0 and report.failures


def test_settings_and_llm_factory(monkeypatch):
    monkeypatch.setenv("CLOUDQUOTE_EXTRACTOR", "llm")
    monkeypatch.delenv("CLOUDQUOTE_LLM_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="needs CLOUDQUOTE_LLM_PROVIDER"):
        Settings.from_env()
    assert build_llm("none") is None
    assert build_llm("openai", base_url="http://localhost:11434/v1", model="llama3.2").model == "llama3.2"
    with pytest.raises(ValueError):
        build_llm("gemini")
    assert "secret" not in repr(Settings(openai_api_key="secret"))


def test_service_from_requirement_json(catalog):
    est = QuoteService.offline(catalog).from_requirement('{"storage": {"size_gb": 100}, "providers": ["aws"]}')
    assert list(est.quotes) == ["aws"] and est.quotes["aws"].monthly_total == Decimal("2.3")


def test_cli_quote_text(capsys):
    assert main(["--env-file", "none.env", "quote", "2 vCPUs and 8 GiB RAM, 24/7, plus 1 TB of files"]) == 0
    out = capsys.readouterr().out
    assert "SAMPLE PRICES" in out and "monthly total: $93.63" in out and "730 hours/month x 0.096 USD/hour" in out


def test_cli_quote_json_and_spec(tmp_path, capsys):
    assert main(["--env-file", "none.env", "quote", "--json", "500 GB of photos on AWS only"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["quotes"]["aws"]["monthly_total_usd"] == "11.50" and data["cheapest"] == "aws"
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"egress_gb_per_month": 1000}), encoding="utf-8")
    assert main(["--env-file", "none.env", "quote", "--json", "--spec", str(spec)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["quotes"]["aws"]["monthly_total_usd"] == "81.00"     # (1000 - 100 free) x 0.09
    assert data["quotes"]["gcp"]["monthly_total_usd"] == "120.00"


def test_cli_errors_are_clean(capsys):
    assert main(["--env-file", "none.env", "quote", "make it cheap"]) == 1
    assert "error:" in capsys.readouterr().err


def test_cli_catalog_and_eval(capsys):
    assert main(["--env-file", "none.env", "catalog"]) == 0
    assert "aws:s3-standard" in capsys.readouterr().out
    assert main(["--env-file", "none.env", "eval", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["n"] >= 15


def test_cli_ui_passes_streamlit_options_after_the_separator(monkeypatch):
    calls = []
    monkeypatch.setattr("cloudquote.cli.subprocess.call", lambda cmd: calls.append(cmd) or 0)
    assert main(["--env-file", "none.env", "ui", "--", "--server.port", "8502"]) == 0
    assert main(["--env-file", "none.env", "ui"]) == 0
    first, second = calls
    assert first[2:4] == ["streamlit", "run"] and first[-2:] == ["--server.port", "8502"]
    assert "--" not in first
    assert second[-1].endswith("streamlit_app.py")
