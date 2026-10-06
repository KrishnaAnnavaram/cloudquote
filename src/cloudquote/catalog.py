"""A versioned, validated pricing catalog.

Every SKU declares its ``kind`` and the schema pins the billing unit to it: compute is priced per
``hour``, storage per ``GB-month`` and egress per ``GB``. A storage price can therefore never be
shown or multiplied as an hourly rate (the prototype labelled S3 prices "/hr").
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .spec import AccessPattern, Provider

DEFAULT_CATALOG = Path(__file__).resolve().parent / "data" / "sample_catalog.json"


class _Sku(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Provider
    sku_id: str = Field(min_length=3)
    name: str
    price: Decimal = Field(ge=0, description="Price in the catalog currency per ``unit``")

    @model_validator(mode="after")
    def _id_matches_provider(self):
        if not self.sku_id.startswith(f"{self.provider}:"):
            raise ValueError(f"sku_id {self.sku_id!r} must start with '{self.provider}:'")
        return self


class ComputeSku(_Sku):
    kind: Literal["compute"]
    unit: Literal["hour"]
    family: str
    vcpus: int = Field(ge=1)
    memory_gib: float = Field(gt=0)
    gpus: int = Field(default=0, ge=0)
    gpu_model: str | None = None
    burstable: bool = False


class StorageSku(_Sku):
    kind: Literal["storage"]
    unit: Literal["GB-month"]
    access_tier: AccessPattern
    min_storage_days: int = Field(default=0, ge=0)
    retrieval_per_gb: Decimal = Field(default=Decimal(0), ge=0)


class EgressSku(_Sku):
    kind: Literal["egress"]
    unit: Literal["GB"]
    free_gb_per_month: Decimal = Field(default=Decimal(0), ge=0)


Sku = Annotated[Union[ComputeSku, StorageSku, EgressSku], Field(discriminator="kind")]


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    catalog_version: str = Field(min_length=1)
    as_of: date
    currency: Literal["USD"] = "USD"
    is_sample: bool
    disclaimer: str = ""
    regions: dict[Provider, str]
    skus: tuple[Sku, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "Catalog":
        ids = [s.sku_id for s in self.skus]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate sku_id(s): {dupes}")
        missing = sorted({s.provider for s in self.skus} - set(self.regions))
        if missing:
            raise ValueError(f"no region declared for provider(s) {missing}")
        if self.is_sample and "sample" not in self.disclaimer.lower():
            raise ValueError("a sample catalog must carry a disclaimer that says 'sample'")
        return self

    # ---- lookups ---------------------------------------------------------------------------
    def get(self, sku_id: str) -> ComputeSku | StorageSku | EgressSku:
        for s in self.skus:
            if s.sku_id == sku_id:
                return s
        raise KeyError(sku_id)

    def compute(self, provider: str) -> list[ComputeSku]:
        return [s for s in self.skus if isinstance(s, ComputeSku) and s.provider == provider]

    def storage(self, provider: str) -> list[StorageSku]:
        return [s for s in self.skus if isinstance(s, StorageSku) and s.provider == provider]

    def egress(self, provider: str) -> EgressSku | None:
        found = [s for s in self.skus if isinstance(s, EgressSku) and s.provider == provider]
        return min(found, key=lambda s: s.price) if found else None

    def providers(self) -> list[str]:
        return sorted(self.regions)


def load_catalog(path: str | Path | None = None) -> Catalog:
    p = Path(path) if path else DEFAULT_CATALOG
    if not p.is_file():
        raise FileNotFoundError(f"pricing catalog not found: {p}")
    return Catalog.model_validate(json.loads(p.read_text(encoding="utf-8")))
