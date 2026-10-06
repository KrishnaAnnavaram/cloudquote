"""Evaluation on labelled requirements: field-level extraction accuracy, SKU accuracy, cost error.

Expected monthly costs in the eval set were computed by hand from the catalog (quantity x rate),
independently of the package code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .catalog import Catalog
from .cost import estimate
from .extract import ExtractionError, Extractor

DEFAULT_EVAL_SET = Path(__file__).resolve().parent / "data" / "eval_set.jsonl"

FIELDS = {
    "vcpus": ("compute", "vcpus"), "memory_gib": ("compute", "memory_gib"), "gpus": ("compute", "gpus"),
    "count": ("compute", "count"), "hours_per_month": ("compute", "hours_per_month"),
    "workload": ("compute", "workload"), "size_gb": ("storage", "size_gb"), "access": ("storage", "access"),
    "retrieval_gb_per_month": ("storage", "retrieval_gb_per_month"),
    "egress_gb_per_month": (None, "egress_gb_per_month"), "term_months": (None, "term_months"),
    "providers": (None, "providers"),
}


@dataclass
class EvalReport:
    n: int
    extraction_failures: int
    field_accuracy: dict[str, float]
    sku_accuracy: float
    cost_mae_usd: float              # over items whose extraction succeeded
    cost_max_abs_error_usd: float
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _get(req, path: tuple[str | None, str]):
    section, name = path
    obj = getattr(req, section) if section else req
    if obj is None:
        return None
    value = getattr(obj, name)
    return list(value) if isinstance(value, tuple) else value


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= 0.01 * max(1.0, abs(float(b)))
        except (TypeError, ValueError):
            return False
    return a == b


def load_eval_set(path: str | Path = DEFAULT_EVAL_SET) -> list[dict]:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    for i, row in enumerate(rows, 1):
        missing = {"id", "text", "expected"} - set(row)
        if missing:
            raise ValueError(f"{path}:{i} missing {sorted(missing)}")
    return rows


def evaluate(rows: list[dict], extractor: Extractor, catalog: Catalog) -> EvalReport:
    if not rows:
        raise ValueError("empty eval set")
    field_hits: dict[str, list[bool]] = {}
    sku_hits: list[bool] = []
    cost_errors: list[Decimal] = []
    failures: list[str] = []
    extraction_failures = 0
    for row in rows:
        try:
            req = extractor.extract(row["text"]).requirement
        except ExtractionError as exc:
            failures.append(f"{row['id']}: extraction failed ({exc})")
            extraction_failures += 1
            for name in row["expected"]:
                field_hits.setdefault(name, []).append(False)
            sku_hits.extend(False for picks in row.get("expected_skus", {}).values() for _ in picks)
            continue
        for name, want in row["expected"].items():
            ok = _same(_get(req, FIELDS[name]), want)
            field_hits.setdefault(name, []).append(ok)
            if not ok:
                failures.append(f"{row['id']}: {name} = {_get(req, FIELDS[name])!r}, expected {want!r}")
        est = estimate(catalog, req)
        for provider, picks in row.get("expected_skus", {}).items():
            quote = est.quotes.get(provider)
            for category, sku_id in picks.items():
                got = quote.selected(category) if quote else None
                sku_hits.append(got == sku_id)
                if got != sku_id:
                    failures.append(f"{row['id']}: {provider} {category} = {got}, expected {sku_id}")
        for provider, want in row.get("expected_monthly_usd", {}).items():
            quote = est.quotes.get(provider)
            got = quote.monthly_total if quote else Decimal(0)
            cost_errors.append(abs(got - Decimal(want)))
    return EvalReport(
        n=len(rows),
        extraction_failures=extraction_failures,
        field_accuracy={k: round(sum(v) / len(v), 3) for k, v in sorted(field_hits.items())},
        sku_accuracy=round(sum(sku_hits) / len(sku_hits), 3) if sku_hits else 0.0,
        cost_mae_usd=float(round(sum(cost_errors) / len(cost_errors), 4)) if cost_errors else 0.0,
        cost_max_abs_error_usd=float(round(max(cost_errors), 4)) if cost_errors else 0.0,
        failures=failures,
    )
