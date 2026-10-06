"""The versioned catalog schema (units pinned to SKU kind) and deterministic SKU matching."""
import pytest
from pydantic import ValidationError

from cloudquote.catalog import Catalog
from cloudquote.matcher import NoMatchingSku, compute_candidates, match_compute, match_storage
from cloudquote.spec import ComputeSpec, StorageSpec


def test_bundled_catalog_is_a_labelled_versioned_sample(catalog):
    assert catalog.is_sample and "SAMPLE PRICES" in catalog.disclaimer and "verify" in catalog.disclaimer.lower()
    assert catalog.catalog_version.startswith("sample-")
    assert catalog.regions == {"aws": "us-east-1", "gcp": "us-central1"}
    assert {s.unit for s in catalog.skus if s.kind == "storage"} == {"GB-month"}
    assert {s.unit for s in catalog.skus if s.kind == "compute"} == {"hour"}


def test_storage_sku_cannot_be_priced_per_hour(raw_catalog):
    sku = next(s for s in raw_catalog["skus"] if s["kind"] == "storage")
    sku["unit"] = "hour"
    with pytest.raises(ValidationError):
        Catalog.model_validate(raw_catalog)


@pytest.mark.parametrize("mutate, message", [
    (lambda c: c["skus"].append(dict(c["skus"][0])), "duplicate"),
    (lambda c: c["skus"][0].update(sku_id="gcp:t3.micro"), "must start with"),
    (lambda c: c.update(disclaimer="illustrative prices"), "sample"),
    (lambda c: c["regions"].pop("gcp"), "no region"),
    (lambda c: c["skus"][0].update(price="-1"), "greater than or equal"),
])
def test_catalog_validation(raw_catalog, mutate, message):
    mutate(raw_catalog)
    with pytest.raises(ValidationError, match=message):
        Catalog.model_validate(raw_catalog)


def test_cheapest_sku_meeting_all_constraints(catalog):
    spec = ComputeSpec(vcpus=4, memory_gib=16)
    sku = match_compute(catalog, "aws", spec)
    assert sku.sku_id == "aws:m5.xlarge"
    for other in compute_candidates(catalog, "aws", spec):
        assert other.vcpus >= 4 and other.memory_gib >= 16 and other.price >= sku.price


def test_no_gpu_skus_unless_requested(catalog):
    assert all(s.gpus == 0 for s in compute_candidates(catalog, "gcp", ComputeSpec(vcpus=4, memory_gib=16)))
    assert match_compute(catalog, "gcp", ComputeSpec(vcpus=4, memory_gib=16, gpus=1)).sku_id == "gcp:g2-standard-4"


def test_burstable_only_for_bursty_workloads(catalog):
    steady = match_compute(catalog, "aws", ComputeSpec(vcpus=2, memory_gib=4))
    bursty = match_compute(catalog, "aws", ComputeSpec(vcpus=2, memory_gib=4, workload="burstable"))
    assert steady.sku_id == "aws:c5.large" and bursty.sku_id == "aws:t3.medium"


def test_memory_heavy_request_picks_high_memory_family(catalog):
    assert match_compute(catalog, "aws", ComputeSpec(vcpus=8, memory_gib=64)).sku_id == "aws:r5.2xlarge"
    assert match_compute(catalog, "gcp", ComputeSpec(vcpus=8, memory_gib=64)).sku_id == "gcp:n2-highmem-8"


def test_no_match_raises(catalog):
    with pytest.raises(NoMatchingSku):
        match_compute(catalog, "aws", ComputeSpec(vcpus=64, memory_gib=512))


@pytest.mark.parametrize("access, aws, gcp", [
    ("frequent", "aws:s3-standard", "gcp:gcs-standard"),
    ("infrequent", "aws:s3-standard-ia", "gcp:gcs-nearline"),
    ("rare", "aws:s3-glacier-ir", "gcp:gcs-coldline"),
    ("archive", "aws:s3-glacier-deep", "gcp:gcs-archive"),
])
def test_storage_tier_mapping(catalog, access, aws, gcp):
    spec = StorageSpec(size_gb=100, access=access)
    assert match_storage(catalog, "aws", spec).sku_id == aws
    assert match_storage(catalog, "gcp", spec).sku_id == gcp
