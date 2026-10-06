"""Reference problems 5 and 7: structured, validated extraction with no account-specific model."""
import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cloudquote.extract import EXTRACT_SYSTEM, ExtractionError, LLMExtractor, RuleBasedExtractor
from cloudquote.llm import LLMError, ScriptedLLM
from cloudquote.spec import ComputeSpec, Requirement
from cloudquote.units import hours_per_month, months_from, to_gb

rules = RuleBasedExtractor()


def test_unit_conversions():
    assert to_gb(10, "TB") == Decimal(10240) and to_gb(512, "MiB") == Decimal("0.5")
    assert hours_per_month(24, "day") == Decimal(730)
    assert hours_per_month(168, "week") == Decimal(730)
    assert hours_per_month(720, "month") == Decimal(720)
    assert months_from(1, "year") == 12 and round(months_from(30, "days"), 4) == Decimal("0.9863")
    for bad in [(25, "day"), (200, "week"), (800, "month")]:
        with pytest.raises(ValueError):
            hours_per_month(*bad)


def test_full_sentence_extraction():
    ex = rules.extract("Real-time analytics: 4 vCPUs and 16 GiB RAM with sustained high CPU, 720 hours per month, "
                       "plus 10 TB of object storage with millisecond access for 30 days.")
    r = ex.requirement
    assert (r.compute.vcpus, r.compute.memory_gib, r.compute.hours_per_month) == (4, 16, 720)
    assert r.compute.workload == "compute"
    assert (r.storage.size_gb, r.storage.access) == (10240, "frequent")
    assert r.term_months == pytest.approx(0.9863, abs=1e-4)


def test_ram_is_not_mistaken_for_storage_and_egress_is_separate():
    r = rules.extract("2 vCPUs, 8 GB RAM, 3 TB of assets and 1 TB egress to the internet per month").requirement
    assert r.compute.memory_gib == 8
    assert r.storage.size_gb == 3072
    assert r.egress_gb_per_month == 1024


def test_impossible_hours_are_rejected():
    with pytest.raises(ExtractionError, match="168"):
        rules.extract("2 vCPUs, 4 GiB RAM for 200 hours per week")


def test_assumptions_are_reported():
    ex = rules.extract("A small server with 2 vCPUs")
    assert ex.requirement.compute.memory_gib == 8
    assert any("RAM" in a for a in ex.assumptions) and any("always on" in a for a in ex.assumptions)


def test_provider_restriction_and_access_patterns():
    assert rules.extract("1 TB of backups on GCP only").requirement.providers == ("gcp",)
    assert rules.extract("1 TB of backups").requirement.storage.access == "infrequent"
    assert rules.extract("5 TB archive for compliance").requirement.storage.access == "archive"


def test_nothing_to_price_is_an_error():
    with pytest.raises(ExtractionError):
        rules.extract("Make it fast and cheap please")
    with pytest.raises(ExtractionError):
        rules.extract("   ")


def test_schema_rejects_bad_specs():
    with pytest.raises(ValidationError):
        Requirement()
    with pytest.raises(ValidationError):
        ComputeSpec(vcpus=0, memory_gib=4)
    with pytest.raises(ValidationError):
        ComputeSpec(vcpus=2, memory_gib=4, hours_per_month=800)
    with pytest.raises(ValidationError):
        Requirement.model_validate({"compute": {"vcpus": 2, "memory_gib": 4, "instance": "m5.large"}})


GOOD = {"compute": {"vcpus": 2, "memory_gib": 8, "hours_per_month": 730}, "term_months": 1}


def test_llm_extractor_accepts_valid_fenced_json():
    llm = ScriptedLLM(["```json\n" + json.dumps(GOOD) + "\n```"])
    ex = LLMExtractor(llm).extract("a 2 vCPU 8 GiB server")
    assert ex.requirement.compute.vcpus == 2 and ex.method == "llm:scripted"
    assert llm.calls[0]["system"] == EXTRACT_SYSTEM and "Do not recommend" in EXTRACT_SYSTEM
    assert llm.calls[0]["json_schema"]["title"] == "Requirement"


def test_llm_extractor_retries_with_the_validation_error():
    bad = {"compute": {"vcpus": 2}}                           # missing memory_gib
    llm = ScriptedLLM([json.dumps(bad), json.dumps(GOOD)])
    ex = LLMExtractor(llm).extract("a 2 vCPU 8 GiB server")
    assert ex.requirement.compute.memory_gib == 8
    assert "compute.memory_gib" in llm.calls[1]["prompt"]


def test_llm_extractor_falls_back_to_rules_instead_of_crashing():
    llm = ScriptedLLM(["Sure! You want an m5.large.", '{"aws": {"instance": "m5.large"}}'])
    ex = LLMExtractor(llm).extract("2 vCPUs and 8 GiB RAM, 24/7")
    assert ex.method == "rules" and ex.requirement.compute.vcpus == 2
    assert "rejected" in ex.assumptions[0]


def test_llm_errors_fall_back_too():
    class Down:
        name = "down"

        def complete(self, *a, **k):
            raise LLMError("HTTP 429")

    ex = LLMExtractor(Down()).extract("500 GB of frequently read files")
    assert ex.method == "rules" and ex.requirement.storage.size_gb == 500
