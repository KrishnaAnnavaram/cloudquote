"""Reference problems 1-3: real totals, correct units, and prices that always belong to the chosen SKU."""
from dataclasses import replace
from decimal import Decimal

import pytest

from cloudquote.cost import LineItem, UnitMismatch, estimate, quote_provider, verify_quote
from cloudquote.spec import ComputeSpec, Requirement, StorageSpec
from cloudquote.units import money


def req(**kw) -> Requirement:
    return Requirement(**kw)


def test_compute_is_hours_times_hourly_rate(catalog):
    q = quote_provider(catalog, req(compute=ComputeSpec(vcpus=4, memory_gib=16, hours_per_month=720)), "aws")
    (line,) = q.lines
    assert line.sku_id == "aws:m5.xlarge"
    assert (line.quantity_unit, line.rate_unit) == ("hours/month", "USD/hour")
    assert line.quantity == Decimal(720) and line.rate == Decimal("0.192")
    assert q.monthly_total == Decimal("138.240")          # 720 h x $0.192/h, exact


def test_instance_count_multiplies_hours(catalog):
    q = quote_provider(catalog, req(compute=ComputeSpec(vcpus=8, memory_gib=16, count=3, hours_per_month=200,
                                                        workload="compute")), "aws")
    assert q.lines[0].sku_id == "aws:c5.2xlarge"
    assert q.lines[0].quantity == Decimal(600)
    assert q.monthly_total == Decimal("204.00")            # 3 x 200 h x $0.34


def test_storage_is_gb_times_gb_month_rate_never_hourly(catalog):
    q = quote_provider(catalog, req(storage=StorageSpec(size_gb=10 * 1024)), "aws")
    (line,) = q.lines
    assert (line.quantity_unit, line.rate_unit) == ("GB", "USD/GB-month")
    assert line.rate_unit != "USD/hour"
    assert q.monthly_total == Decimal("235.520")           # 10 TB = 10240 GB x $0.023


def test_hand_computed_full_quote(catalog):
    r = req(compute=ComputeSpec(vcpus=4, memory_gib=16, hours_per_month=720, workload="compute"),
            storage=StorageSpec(size_gb=10240))
    est = estimate(catalog, r)
    assert est.quotes["aws"].monthly_total == Decimal("373.760")   # 138.24 + 235.52
    assert est.quotes["gcp"].monthly_total == Decimal("301.280")   # 720 x 0.134 + 10240 x 0.020
    assert est.cheapest == "gcp"


def test_egress_free_tier_and_rates(catalog):
    r = req(egress_gb_per_month=1024)
    aws, gcp = quote_provider(catalog, r, "aws"), quote_provider(catalog, r, "gcp")
    assert aws.lines[0].quantity == Decimal(924)           # first 100 GB free
    assert aws.monthly_total == Decimal("83.16")
    assert gcp.monthly_total == Decimal("122.88")          # 1024 x $0.12, no free tier
    assert (aws.lines[0].quantity_unit, aws.lines[0].rate_unit) == ("GB/month", "USD/GB")
    assert quote_provider(catalog, req(egress_gb_per_month=60), "aws").monthly_total == 0


def test_retrieval_uses_the_storage_skus_own_retrieval_rate(catalog):
    r = req(storage=StorageSpec(size_gb=20480, access="rare", retrieval_gb_per_month=100), term_months=6)
    q = quote_provider(catalog, r, "aws")
    storage, retrieval = q.lines
    assert storage.sku_id == retrieval.sku_id == "aws:s3-glacier-ir"
    assert retrieval.rate == Decimal("0.03") and retrieval.rate_unit == "USD/GB"
    assert q.monthly_total == Decimal("84.920")            # 81.92 + 3.00
    assert q.term_total == Decimal("509.520")              # x 6 months


def test_term_total_scales_monthly_lines(catalog):
    r = req(compute=ComputeSpec(vcpus=2, memory_gib=8), term_months=12)
    q = quote_provider(catalog, r, "gcp")
    assert q.monthly_total == Decimal("48.910")            # 730 h x $0.067
    assert q.term_total == Decimal("586.920")


def test_minimum_storage_duration_is_charged_and_flagged(catalog):
    r = req(storage=StorageSpec(size_gb=1000, access="archive"), term_months=1)
    q = quote_provider(catalog, r, "gcp")
    line = q.lines[0]
    assert money(line.billed_months) == Decimal("12.00")   # 365 days minimum
    assert q.monthly_total == Decimal("1.2000")            # monthly figure unchanged
    assert money(q.term_total) == Decimal("14.40")
    assert any("minimum of 365 days" in w for w in q.warnings)


def test_line_items_reject_mismatched_units():
    with pytest.raises(UnitMismatch):
        LineItem("storage", "aws:s3-standard", "x", Decimal(10), "hours/month", Decimal("0.023"), "USD/hour",
                 Decimal(1))
    with pytest.raises(UnitMismatch):
        LineItem("compute", "aws:m5.large", "x", Decimal(10), "GB", Decimal("0.096"), "USD/GB-month", Decimal(1))


def test_verify_quote_detects_a_price_from_another_sku(catalog):
    q = quote_provider(catalog, req(compute=ComputeSpec(vcpus=2, memory_gib=8)), "aws")
    tampered = replace(q.lines[0], rate=catalog.get("aws:m5.xlarge").price)   # m5.large line, m5.xlarge price
    with pytest.raises(ValueError, match="does not match"):
        verify_quote(replace(q, lines=(tampered,)), catalog)
    foreign = replace(q.lines[0], sku_id="gcp:e2-standard-2", rate=catalog.get("gcp:e2-standard-2").price)
    with pytest.raises(ValueError, match="not a aws SKU"):
        verify_quote(replace(q, lines=(foreign,)), catalog)


def test_every_line_rate_comes_from_its_own_sku(catalog):
    r = req(compute=ComputeSpec(vcpus=4, memory_gib=16, gpus=1),
            storage=StorageSpec(size_gb=500, access="rare", retrieval_gb_per_month=10), egress_gb_per_month=300)
    for q in estimate(catalog, r).quotes.values():
        for line in q.lines:
            sku = catalog.get(line.sku_id)
            assert sku.provider == q.provider
            expected = sku.retrieval_per_gb if line.category == "retrieval" else sku.price
            assert line.rate == expected


def test_decimal_arithmetic_has_no_float_drift(catalog):
    r = req(storage=StorageSpec(size_gb=0.1), egress_gb_per_month=0.2)
    q = quote_provider(catalog, r, "gcp")
    assert q.monthly_total == Decimal("0.1") * Decimal("0.020") + Decimal("0.2") * Decimal("0.12")


def test_unpriceable_parts_are_reported_not_guessed(catalog):
    r = req(compute=ComputeSpec(vcpus=4, memory_gib=16, gpus=2), storage=StorageSpec(size_gb=100))
    est = estimate(catalog, r)
    for q in est.quotes.values():
        assert not q.complete and "GPU" in q.unavailable[0]
        assert [line.category for line in q.lines] == ["storage"]
    assert est.cheapest is None


def test_json_output_is_rounded_to_cents(catalog):
    d = quote_provider(catalog, req(storage=StorageSpec(size_gb=2048, access="infrequent"), term_months=1.9726),
                       "aws").as_dict()
    assert d["monthly_total_usd"] == "25.60" and d["lines"][0]["rate"] == "0.0125"
