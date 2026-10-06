"""The validated, structured requirement that every estimate is computed from.

Whatever produces it (the rule-based parser, an LLM, or a person editing JSON) must pass this
schema. A missing or malformed field is a validation error with a clear message, never a
``KeyError`` deep inside the cost code.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Provider = Literal["aws", "gcp"]
AccessPattern = Literal["frequent", "infrequent", "rare", "archive"]
Workload = Literal["general", "compute", "memory", "burstable"]

PROVIDERS: tuple[str, ...] = ("aws", "gcp")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ComputeSpec(_Strict):
    vcpus: int = Field(ge=1, le=448, description="Minimum vCPUs per instance")
    memory_gib: float = Field(gt=0, le=12288, description="Minimum RAM per instance, GiB")
    gpus: int = Field(default=0, ge=0, le=16, description="Minimum GPUs per instance")
    count: int = Field(default=1, ge=1, le=1000, description="Number of identical instances")
    hours_per_month: float = Field(default=730, gt=0, le=730, description="Running hours per month, per instance")
    workload: Workload = Field(default="general", description="Steady 'compute' excludes burstable families")


class StorageSpec(_Strict):
    size_gb: float = Field(gt=0, description="Stored data, GB (1 TB = 1024 GB)")
    access: AccessPattern = Field(default="frequent", description="How often the data is read")
    retrieval_gb_per_month: float = Field(default=0, ge=0, description="Data read back per month, GB")


class Requirement(_Strict):
    compute: ComputeSpec | None = None
    storage: StorageSpec | None = None
    egress_gb_per_month: float = Field(default=0, ge=0, description="Internet egress per month, GB")
    term_months: float = Field(default=1, gt=0, le=120, description="Estimate horizon in months")
    providers: tuple[Provider, ...] = Field(default=PROVIDERS, min_length=1)

    @model_validator(mode="after")
    def _something_to_price(self) -> "Requirement":
        if self.compute is None and self.storage is None and self.egress_gb_per_month == 0:
            raise ValueError("the requirement has no compute, storage or egress to price")
        if len(set(self.providers)) != len(self.providers):
            raise ValueError("providers must not repeat")
        return self


def llm_json_schema() -> dict:
    """JSON schema handed to LLM extractors (the same model that validates their output)."""
    return Requirement.model_json_schema()
