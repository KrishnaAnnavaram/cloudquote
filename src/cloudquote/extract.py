"""Turn a plain-English requirement into a validated ``Requirement``.

* ``RuleBasedExtractor`` is deterministic and offline (the default, and what tests use).
* ``LLMExtractor`` asks a model for JSON, validates it against the same pydantic schema, retries
  once with the validation error, and finally falls back to the rule-based parser.

Both report the assumptions they made (e.g. a default RAM size), so a quote never silently
depends on a guess.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from pydantic import ValidationError

from .llm import LLM, LLMError
from .spec import ComputeSpec, Requirement, StorageSpec, llm_json_schema
from .units import HOURS_PER_MONTH, hours_per_month, months_from, to_gb

# "1,000" and "2,500.5" use a thousands separator. Any other comma is a decimal comma ("1,5" = 1.5).
_THOUSANDS = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"
_NUM = r"(" + _THOUSANDS + r"|\d+(?:[.,]\d+)?)"
_SIZE = _NUM + r"\s*(pib|pb|tib|tb|gib|gb|mib|mb)\b"
_VCPU_RE = re.compile(_NUM + r"\s*(?:x\s*)?(?:v\s*cpus?|vcpus?|cpus?|cores?|virtual cpus?)\b", re.I)
_RAM_RE = re.compile(_NUM + r"\s*(gib|gb|g)\b\s*(?:of\s+)?(?:ram|memory)", re.I)
_RAM_RE2 = re.compile(r"(?:ram|memory)\s*(?:of|:)?\s*" + _NUM + r"\s*(gib|gb)\b", re.I)
_GPU_RE = re.compile(r"(\d+)\s*(?:x\s*)?(?:nvidia\s+)?(?:[a-z]\d+\s+)?gpus?\b", re.I)
_COUNT_RE = re.compile(r"(\d+)\s*(?:identical\s+)?(?:instances|vms|virtual machines|servers|nodes|machines)\b", re.I)
_HOURS_RE = re.compile(_NUM + r"\s*(?:hours?|hrs?|h)\s*(?:per|a|/|each|every)?\s*(day|week|month)\b", re.I)
_HOURS_MONTHLY_RE = re.compile(_NUM + r"\s*(?:hours?|hrs?|h)\s*(monthly|weekly|daily)\b", re.I)
_ALWAYS_ON_RE = re.compile(r"24\s*/\s*7|24x7|around the clock|always[- ]on|continuously|non-?stop", re.I)
_SIZE_RE = re.compile(_SIZE, re.I)
_TERM_RE = re.compile(r"(?:for|over|during|retain(?:ed)?\s+for|kept\s+for)\s+(?:the\s+next\s+)?" + _NUM
                      + r"\s*(days?|weeks?|months?|years?)\b", re.I)
_EGRESS_HINT = re.compile(r"egress|transfer(?:red)?\s+out|outbound|data\s+out|download(?:ed|s)?\s+by|served\s+to", re.I)
_RETRIEVAL_HINT = re.compile(r"retriev|read\s+back|restor", re.I)
_RAM_HINT = re.compile(r"^\s*(?:of\s+)?(?:ram|memory)", re.I)

_ACCESS_RULES: list[tuple[str, re.Pattern]] = [
    ("archive", re.compile(r"archiv|compliance|once a year|yearly access|rarely if ever|deep cold", re.I)),
    ("rare", re.compile(r"\bcold\b|rarely|seldom|once a quarter|quarterly|disaster recovery", re.I)),
    ("infrequent", re.compile(r"infrequent|occasional|once a month|monthly access|\bbackups?\b|cool", re.I)),
]
_WORKLOAD_RULES: list[tuple[str, re.Pattern]] = [
    ("compute", re.compile(r"compute[- ]intensive|cpu[- ]bound|high cpu|sustained (?:high )?cpu|\bhpc\b|"
                           r"batch processing|encoding|transcod", re.I)),
    ("memory", re.compile(r"in-memory|memory[- ]intensive|large ram|ram-heavy|cach(?:e|ing)", re.I)),
    ("burstable", re.compile(r"burst|\bdev\b|development|testing|staging|low[- ]traffic|hobby|tiny|"
                             r"occasional cpu|small website", re.I)),
]
_ONLY_RE = re.compile(r"\b(?:only|just)\s+(aws|amazon|gcp|google cloud)\b|\b(aws|amazon|gcp|google cloud)\s+only\b", re.I)


class ExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class Extraction:
    requirement: Requirement
    method: str
    assumptions: tuple[str, ...] = field(default=())


class Extractor(Protocol):
    def extract(self, text: str) -> Extraction: ...


def _num(s: str) -> Decimal:
    if re.fullmatch(_THOUSANDS, s):
        return Decimal(s.replace(",", ""))
    return Decimal(s.replace(",", "."))


def _size_mentions(text: str) -> list[tuple[Decimal, str, int, int]]:
    """All size mentions as (GB, role, start, end) where role is ram/egress/retrieval/storage."""
    out = []
    for m in _SIZE_RE.finditer(text):
        gb = to_gb(_num(m.group(1)), m.group(2))
        before = text[max(0, m.start() - 40):m.start()]
        after = text[m.end():m.end() + 40]
        if _RAM_HINT.match(after) or re.search(r"(?:ram|memory)\s*(?:of|:)?\s*$", before, re.I):
            role = "ram"
        elif _EGRESS_HINT.search(before) or _EGRESS_HINT.search(after[:25]):
            role = "egress"
        elif _RETRIEVAL_HINT.search(before[-25:]):
            role = "retrieval"
        else:
            role = "storage"
        out.append((gb, role, m.start(), m.end()))
    return out


class RuleBasedExtractor:
    name = "rules"

    def extract(self, text: str) -> Extraction:
        if not text or not text.strip():
            raise ExtractionError("the requirement text is empty")
        t = " ".join(text.split())
        notes: list[str] = []

        vcpus = max((int(_num(m.group(1))) for m in _VCPU_RE.finditer(t)), default=None)
        ram_matches = list(_RAM_RE.finditer(t)) + list(_RAM_RE2.finditer(t))
        memory = max((float(_num(m.group(1))) for m in ram_matches), default=None)
        gpus = max((int(m.group(1)) for m in _GPU_RE.finditer(t)), default=0)
        if gpus == 0 and re.search(r"\bgpus?\b|cuda|deep[- ]learning (?:training|inference)", t, re.I):
            gpus = 1
            notes.append("a GPU workload without a count: assumed 1 GPU")

        compute = None
        if vcpus or memory or gpus:
            if vcpus is None:
                vcpus = 4 if gpus else 1
                notes.append(f"no vCPU count given: assumed {vcpus}")
            if memory is None:
                memory = float(vcpus * 4)
                notes.append(f"no RAM given: assumed {memory:g} GiB (4 GiB per vCPU)")
            try:
                hours = self._hours(t, notes)
            except ValueError as exc:
                raise ExtractionError(f"impossible running hours: {exc}") from exc
            count_m = _COUNT_RE.search(t)
            workload = next((w for w, rx in _WORKLOAD_RULES if rx.search(t)), "general")
            if re.search(r"sustained|steady|predictable|production", t, re.I) and workload == "burstable":
                workload = "general"
            compute = ComputeSpec(vcpus=vcpus, memory_gib=memory, gpus=gpus,
                                  count=int(count_m.group(1)) if count_m else 1,
                                  hours_per_month=float(hours), workload=workload)

        sizes = _size_mentions(t)
        storage_gb = next((gb for gb, role, *_ in sizes if role == "storage"), None)
        egress = sum((gb for gb, role, *_ in sizes if role == "egress"), Decimal(0))
        retrieval = sum((gb for gb, role, *_ in sizes if role == "retrieval"), Decimal(0))
        storage = None
        if storage_gb is not None:
            access = next((a for a, rx in _ACCESS_RULES if rx.search(t)), "frequent")
            storage = StorageSpec(size_gb=float(storage_gb), access=access,
                                  retrieval_gb_per_month=float(retrieval))

        term = Decimal(1)
        term_m = _TERM_RE.search(t)
        if term_m:
            term = months_from(_num(term_m.group(1)), term_m.group(2))
        else:
            notes.append("no duration given: estimated for 1 month")

        providers = ("aws", "gcp")
        only = _ONLY_RE.search(t)
        if only:
            word = (only.group(1) or only.group(2)).lower()
            providers = ("aws",) if word in {"aws", "amazon"} else ("gcp",)

        try:
            req = Requirement(compute=compute, storage=storage, egress_gb_per_month=float(egress),
                              term_months=float(round(term, 4)), providers=providers)
        except ValidationError as exc:
            raise ExtractionError(f"could not build a valid requirement: {exc.errors()[0]['msg']}") from exc
        return Extraction(req, self.name, tuple(notes))

    @staticmethod
    def _hours(t: str, notes: list[str]) -> Decimal:
        m = _HOURS_RE.search(t)
        if m:
            return hours_per_month(_num(m.group(1)), m.group(2))
        m = _HOURS_MONTHLY_RE.search(t)
        if m:
            return hours_per_month(_num(m.group(1)), m.group(2))
        if re.search(r"business hours|office hours|working hours", t, re.I):
            notes.append("business hours: assumed 8 h/day, 5 days/week")
            return hours_per_month(40, "week")
        if not _ALWAYS_ON_RE.search(t):
            notes.append(f"no running hours given: assumed always on ({HOURS_PER_MONTH} h/month)")
        return HOURS_PER_MONTH


EXTRACT_SYSTEM = (
    "Extract cloud infrastructure requirements into JSON that matches the given schema exactly. "
    "Do not recommend instance types or prices. Conventions: memory in GiB; storage in GB with "
    "1 TB = 1024 GB; hours_per_month is per instance and at most 730 (24/7 = 730, N hours per week = "
    "N*730/168, N hours per day = N*730/24); term_months is the estimate horizon (30.4167 days = 1 "
    "month); access is one of frequent, infrequent, rare, archive; omit compute or storage if not "
    "requested. Reply with the JSON object only."
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


class LLMExtractor:
    def __init__(self, llm: LLM, *, fallback: Extractor | None = None, retries: int = 1) -> None:
        self.llm = llm
        self.fallback = fallback if fallback is not None else RuleBasedExtractor()
        self.retries = max(0, retries)
        self.name = f"llm:{llm.name}"

    def extract(self, text: str) -> Extraction:
        if not text or not text.strip():
            raise ExtractionError("the requirement text is empty")
        schema = llm_json_schema()
        prompt = f"Schema:\n{json.dumps(schema)}\n\nRequirement:\n{text.strip()}"
        errors: list[str] = []
        for _ in range(self.retries + 1):
            try:
                raw = self.llm.complete(prompt, system=EXTRACT_SYSTEM, json_schema=schema)
                req = Requirement.model_validate_json(_FENCE_RE.sub("", raw.strip()))
                return Extraction(req, self.name)
            except ValidationError as exc:
                first = exc.errors()[0]
                msg = f"{'.'.join(str(p) for p in first['loc']) or 'output'}: {first['msg']}"
                errors.append(msg)
                prompt += f"\n\nYour previous reply was invalid ({msg}). Return corrected JSON only."
            except LLMError as exc:
                errors.append(str(exc))
                break
        result = self.fallback.extract(text)
        note = "LLM extraction rejected (" + "; ".join(errors) + "); used the rule-based parser"
        return Extraction(result.requirement, result.method, (note,) + result.assumptions)
