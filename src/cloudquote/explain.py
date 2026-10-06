"""Human-readable explanations of a computed estimate.

The deterministic explanation is always available. An optional LLM rewrite is accepted only if
every dollar figure it mentions appears in the computed quote; otherwise it is discarded, so the
prose can never contradict the numbers.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .cost import Estimate
from .llm import LLM, LLMError
from .units import fmt_money, money

_DOLLAR_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")


def explain(est: Estimate) -> str:
    parts = []
    for provider, q in est.quotes.items():
        if not q.lines:
            parts.append(f"{provider.upper()}: nothing could be priced ({'; '.join(q.unavailable)}).")
            continue
        picks = ", ".join(f"{line.category} {line.sku_id.split(':', 1)[1]}" for line in q.lines
                          if line.category in {"compute", "storage"})
        parts.append(f"{provider.upper()} ({q.region}): {picks or 'egress only'}, "
                     f"{fmt_money(q.monthly_total)} per month, {fmt_money(q.term_total)} over "
                     f"{money(q.term_months)} months.")
        if q.unavailable:
            parts.append(f"  Not priced: {'; '.join(q.unavailable)}")
    if est.cheapest:
        parts.append(f"Cheapest complete quote: {est.cheapest.upper()}.")
    first = next(iter(est.quotes.values()), None)
    if first is not None and first.is_sample:
        parts.append(first.disclaimer)
    return "\n".join(parts)


def _allowed_amounts(est: Estimate) -> set[Decimal]:
    allowed: set[Decimal] = set()
    for q in est.quotes.values():
        allowed |= {money(q.monthly_total), money(q.term_total)}
        for line in q.lines:
            allowed |= {money(line.monthly), money(line.amount), line.rate, money(line.rate)}
    return allowed


def numbers_consistent(text: str, est: Estimate) -> bool:
    allowed = _allowed_amounts(est)
    for m in _DOLLAR_RE.finditer(text):
        try:
            value = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation:
            return False
        if value not in allowed and money(value) not in allowed:
            return False
    return True


def explain_with_llm(est: Estimate, llm: LLM) -> tuple[str, bool]:
    """Return (text, used_llm). Falls back to ``explain`` on errors or inconsistent numbers."""
    base = explain(est)
    prompt = ("Rewrite this cloud cost comparison for a non-expert in at most 5 sentences. Do not "
              "add, change or round any dollar amount; reuse them exactly as written.\n\n" + base)
    try:
        text = llm.complete(prompt).strip()
    except LLMError:
        return base, False
    if not text or not numbers_consistent(text, est):
        return base, False
    return text, True
