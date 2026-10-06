"""Deterministic SKU selection: the cheapest catalog SKU that satisfies every constraint.

The LLM never picks SKUs. It only extracts the requirement, so a recommendation is always a
real catalog entry that provably meets the requested vCPUs, RAM and GPUs.
"""
from __future__ import annotations

from .catalog import Catalog, ComputeSku, StorageSku
from .spec import ComputeSpec, StorageSpec


class NoMatchingSku(LookupError):
    pass


def compute_candidates(catalog: Catalog, provider: str, spec: ComputeSpec) -> list[ComputeSku]:
    out = []
    for sku in catalog.compute(provider):
        if sku.vcpus < spec.vcpus or sku.memory_gib < spec.memory_gib or sku.gpus < spec.gpus:
            continue
        if spec.gpus == 0 and sku.gpus > 0:
            continue            # never pay for accelerators nobody asked for
        if sku.burstable and spec.workload != "burstable":
            continue            # CPU-credit instances only for explicitly bursty workloads
        out.append(sku)
    return sorted(out, key=lambda s: (s.price, s.vcpus, s.memory_gib, s.sku_id))


def match_compute(catalog: Catalog, provider: str, spec: ComputeSpec) -> ComputeSku:
    candidates = compute_candidates(catalog, provider, spec)
    if not candidates:
        raise NoMatchingSku(
            f"no {provider} instance in catalog {catalog.catalog_version} has >= {spec.vcpus} vCPU, "
            f">= {spec.memory_gib:g} GiB RAM and >= {spec.gpus} GPU ({spec.workload} workload)"
        )
    return candidates[0]


def match_storage(catalog: Catalog, provider: str, spec: StorageSpec) -> StorageSku:
    candidates = sorted((s for s in catalog.storage(provider) if s.access_tier == spec.access),
                        key=lambda s: (s.price, s.sku_id))
    if not candidates:
        raise NoMatchingSku(f"no {provider} storage class for '{spec.access}' access in the catalog")
    return candidates[0]
