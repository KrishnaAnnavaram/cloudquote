"""Cost calculation with explicit units and a line-item breakdown.

Each line is ``quantity [quantity_unit] x rate [rate_unit] x billed_months``; only three unit
pairs are legal::

    hours/month x USD/hour      -> USD per month  (compute: instance-hours)
    GB          x USD/GB-month  -> USD per month  (storage at rest)
    GB/month    x USD/GB        -> USD per month  (retrieval, egress)

Every line names exactly one SKU and its rate is read from that SKU, so prices from different
SKUs can never be mixed. ``verify_quote`` re-checks this against the catalog.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from .catalog import Catalog, ComputeSku, EgressSku, StorageSku
from .matcher import NoMatchingSku, match_compute, match_storage
from .spec import Requirement
from .units import DAYS_PER_MONTH, D, money

Category = Literal["compute", "storage", "retrieval", "egress"]

UNIT_PAIRS: dict[Category, tuple[str, str]] = {
    "compute": ("hours/month", "USD/hour"),
    "storage": ("GB", "USD/GB-month"),
    "retrieval": ("GB/month", "USD/GB"),
    "egress": ("GB/month", "USD/GB"),
}


class UnitMismatch(ValueError):
    pass


@dataclass(frozen=True)
class LineItem:
    category: Category
    sku_id: str
    description: str
    quantity: Decimal
    quantity_unit: str
    rate: Decimal
    rate_unit: str
    billed_months: Decimal

    def __post_init__(self) -> None:
        expected = UNIT_PAIRS[self.category]
        if (self.quantity_unit, self.rate_unit) != expected:
            raise UnitMismatch(
                f"{self.category} line must be {expected[0]} x {expected[1]}, "
                f"got {self.quantity_unit} x {self.rate_unit}"
            )
        if self.quantity < 0 or self.rate < 0 or self.billed_months <= 0:
            raise ValueError("quantity and rate must be >= 0 and billed_months > 0")

    @property
    def monthly(self) -> Decimal:
        """Cost for one billing month, unrounded."""
        return self.quantity * self.rate

    @property
    def amount(self) -> Decimal:
        """Cost over the billed months, unrounded."""
        return self.monthly * self.billed_months

    def as_dict(self) -> dict:
        return {
            "category": self.category, "sku_id": self.sku_id, "description": self.description,
            "quantity": str(self.quantity.normalize()), "quantity_unit": self.quantity_unit,
            "rate": str(self.rate), "rate_unit": self.rate_unit,
            "billed_months": str(money(self.billed_months)),
            "monthly_usd": str(money(self.monthly)), "term_usd": str(money(self.amount)),
        }


@dataclass(frozen=True)
class Quote:
    provider: str
    region: str
    catalog_version: str
    as_of: str
    is_sample: bool
    disclaimer: str
    term_months: Decimal
    lines: tuple[LineItem, ...]
    warnings: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()          # parts that could not be priced

    @property
    def complete(self) -> bool:
        return not self.unavailable

    @property
    def monthly_total(self) -> Decimal:
        return sum((line.monthly for line in self.lines), Decimal(0))

    @property
    def term_total(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal(0))

    def selected(self, category: Category) -> str | None:
        return next((line.sku_id for line in self.lines if line.category == category), None)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider, "region": self.region, "catalog_version": self.catalog_version,
            "as_of": self.as_of, "is_sample": self.is_sample, "disclaimer": self.disclaimer,
            "term_months": str(money(self.term_months)),
            "monthly_total_usd": str(money(self.monthly_total)), "term_total_usd": str(money(self.term_total)),
            "complete": self.complete, "lines": [line.as_dict() for line in self.lines],
            "warnings": list(self.warnings), "unavailable": list(self.unavailable),
        }


def _compute_line(sku: ComputeSku, req: Requirement) -> LineItem:
    spec = req.compute
    assert spec is not None
    hours = D(spec.hours_per_month)
    gpu = f", {sku.gpus}x {sku.gpu_model}" if sku.gpus else ""
    return LineItem(
        "compute", sku.sku_id,
        f"{spec.count} x {sku.name} ({sku.vcpus} vCPU, {sku.memory_gib:g} GiB{gpu}) x {hours.normalize():f} h/month",
        hours * spec.count, "hours/month", sku.price, "USD/hour", D(req.term_months),
    )


def _storage_lines(sku: StorageSku, req: Requirement, warnings: list[str]) -> list[LineItem]:
    spec = req.storage
    assert spec is not None
    term = D(req.term_months)
    billed = term
    if sku.min_storage_days:
        minimum = D(sku.min_storage_days) / DAYS_PER_MONTH
        if term < minimum:
            billed = minimum
            warnings.append(
                f"{sku.name} bills a minimum of {sku.min_storage_days} days; storage is charged for "
                f"{money(minimum)} months instead of {money(term)}"
            )
    lines = [LineItem("storage", sku.sku_id, f"{sku.name}: {D(spec.size_gb).normalize():f} GB stored",
                      D(spec.size_gb), "GB", sku.price, "USD/GB-month", billed)]
    if spec.retrieval_gb_per_month > 0 and sku.retrieval_per_gb > 0:
        lines.append(LineItem("retrieval", sku.sku_id,
                              f"{sku.name} retrieval: {D(spec.retrieval_gb_per_month).normalize():f} GB/month",
                              D(spec.retrieval_gb_per_month), "GB/month", sku.retrieval_per_gb, "USD/GB", term))
    return lines


def _egress_line(sku: EgressSku, req: Requirement) -> LineItem:
    total = D(req.egress_gb_per_month)
    billable = max(total - sku.free_gb_per_month, Decimal(0))
    free = f" (first {sku.free_gb_per_month.normalize():f} GB/month free)" if sku.free_gb_per_month else ""
    return LineItem("egress", sku.sku_id, f"{sku.name}: {total.normalize():f} GB/month{free}",
                    billable, "GB/month", sku.price, "USD/GB", D(req.term_months))


def quote_provider(catalog: Catalog, req: Requirement, provider: str) -> Quote:
    if provider not in catalog.regions:
        raise ValueError(f"catalog {catalog.catalog_version} has no prices for {provider!r}")
    lines: list[LineItem] = []
    warnings: list[str] = []
    unavailable: list[str] = []
    if req.compute is not None:
        try:
            lines.append(_compute_line(match_compute(catalog, provider, req.compute), req))
        except NoMatchingSku as exc:
            unavailable.append(str(exc))
    if req.storage is not None:
        try:
            lines.extend(_storage_lines(match_storage(catalog, provider, req.storage), req, warnings))
        except NoMatchingSku as exc:
            unavailable.append(str(exc))
    if req.egress_gb_per_month > 0:
        sku = catalog.egress(provider)
        if sku is None:
            unavailable.append(f"no {provider} egress price in the catalog")
        else:
            lines.append(_egress_line(sku, req))
    quote = Quote(provider, catalog.regions[provider], catalog.catalog_version, catalog.as_of.isoformat(),
                  catalog.is_sample, catalog.disclaimer, D(req.term_months), tuple(lines), tuple(warnings),
                  tuple(unavailable))
    verify_quote(quote, catalog)
    return quote


def verify_quote(quote: Quote, catalog: Catalog) -> None:
    """Assert that every line's rate and unit come from the one SKU it names."""
    expected_unit = {"hour": "USD/hour", "GB-month": "USD/GB-month", "GB": "USD/GB"}
    for line in quote.lines:
        sku = catalog.get(line.sku_id)
        if sku.provider != quote.provider:
            raise ValueError(f"{line.sku_id} is not a {quote.provider} SKU")
        if line.category == "retrieval":
            if not isinstance(sku, StorageSku) or line.rate != sku.retrieval_per_gb:
                raise ValueError(f"retrieval rate on {line.sku_id} does not match the catalog")
            continue
        kind = "storage" if line.category == "storage" else line.category
        if sku.kind != kind or line.rate != sku.price or line.rate_unit != expected_unit[sku.unit]:
            raise ValueError(f"{line.category} line rate/unit does not match catalog SKU {line.sku_id}")


@dataclass(frozen=True)
class Estimate:
    requirement: Requirement
    quotes: dict[str, Quote]
    assumptions: tuple[str, ...] = field(default=())

    @property
    def cheapest(self) -> str | None:
        complete = [q for q in self.quotes.values() if q.complete and q.lines]
        if not complete:
            return None
        return min(complete, key=lambda q: (q.monthly_total, q.provider)).provider

    def as_dict(self) -> dict:
        return {
            "requirement": self.requirement.model_dump(mode="json"),
            "assumptions": list(self.assumptions),
            "quotes": {p: q.as_dict() for p, q in self.quotes.items()},
            "cheapest": self.cheapest,
        }


def estimate(catalog: Catalog, req: Requirement, *, assumptions: tuple[str, ...] = ()) -> Estimate:
    return Estimate(req, {p: quote_provider(catalog, req, p) for p in req.providers}, assumptions)
