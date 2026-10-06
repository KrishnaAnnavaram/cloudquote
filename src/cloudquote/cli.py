"""``cloudquote`` command-line entry point."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from .config import Settings, load_dotenv
from .cost import Estimate
from .evaluate import DEFAULT_EVAL_SET, evaluate, load_eval_set
from .explain import explain, explain_with_llm
from .extract import ExtractionError
from .service import QuoteService
from .units import fmt_money, money


def _print_estimate(est: Estimate) -> None:
    first = next(iter(est.quotes.values()))
    if first.is_sample:
        print(f"!! {first.disclaimer}")
    print(f"catalog {first.catalog_version} (prices as of {first.as_of})\n")
    print("requirement:", json.dumps(est.requirement.model_dump(mode="json")))
    for note in est.assumptions:
        print(f"  assumption: {note}")
    for provider, q in est.quotes.items():
        print(f"\n== {provider.upper()} ({q.region}) ==")
        for line in q.lines:
            print(f"  {line.category:<9} {line.description}")
            print(f"            {line.quantity.normalize():f} {line.quantity_unit} x {line.rate} {line.rate_unit}"
                  f" = {fmt_money(line.monthly)}/month; x {money(line.billed_months)} months"
                  f" = {fmt_money(line.amount)}")
        print(f"  monthly total: {fmt_money(q.monthly_total)}")
        print(f"  total for {money(q.term_months)} months: {fmt_money(q.term_total)}")
        for w in q.warnings:
            print(f"  warning: {w}")
        for u in q.unavailable:
            print(f"  NOT PRICED: {u}")
    if est.cheapest:
        print(f"\ncheapest complete quote: {est.cheapest.upper()}")


def _cmd_quote(settings: Settings, args: argparse.Namespace) -> int:
    service = QuoteService(settings.catalog(), settings.extractor_impl())
    if args.spec:
        est = service.from_requirement(Path(args.spec).read_text(encoding="utf-8"))
    else:
        text = " ".join(args.text) if args.text else sys.stdin.read()
        _, est = service.from_text(text)
    if args.json:
        print(json.dumps(est.as_dict(), indent=2))
        return 0
    _print_estimate(est)
    llm = settings.llm() if settings.explain_with_llm else None
    print("\n" + (explain_with_llm(est, llm)[0] if llm else explain(est)))
    return 0


def _cmd_catalog(settings: Settings, args: argparse.Namespace) -> int:
    cat = settings.catalog()
    print(f"catalog {cat.catalog_version}, as of {cat.as_of}, {cat.currency}, sample={cat.is_sample}")
    if cat.disclaimer:
        print(cat.disclaimer)
    for s in cat.skus:
        extra = f"{s.vcpus} vCPU / {s.memory_gib:g} GiB / {s.gpus} GPU" if s.kind == "compute" else (
            getattr(s, "access_tier", "") or "")
        print(f"  {s.sku_id:<24} {s.kind:<8} {s.price:>10} USD/{s.unit:<9} {extra}")
    return 0


def _cmd_eval(settings: Settings, args: argparse.Namespace) -> int:
    report = evaluate(load_eval_set(args.eval_set), settings.extractor_impl(), settings.catalog())
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
        return 0
    print(f"items: {report.n}  (extraction failures: {report.extraction_failures})")
    print("field accuracy:", json.dumps(report.field_accuracy))
    print(f"SKU accuracy: {report.sku_accuracy:.3f}")
    print(f"monthly cost MAE: ${report.cost_mae_usd:.4f}  (max abs error ${report.cost_max_abs_error_usd:.4f})")
    for f in report.failures:
        print(f"  miss: {f}")
    return 0


def _cmd_ui(settings: Settings, args: argparse.Namespace) -> int:  # pragma: no cover
    app = Path(__file__).parent / "app" / "streamlit_app.py"
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), *args.streamlit_args])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cloudquote", description="Plain-English requirements to AWS/GCP cost quotes")
    p.add_argument("--env-file", default=".env")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("quote", help="estimate monthly cost from text (or stdin) or a JSON spec")
    s.add_argument("text", nargs="*")
    s.add_argument("--spec", help="path to a Requirement JSON file instead of text")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_quote)
    s = sub.add_parser("catalog", help="show the pricing catalog")
    s.set_defaults(func=_cmd_catalog)
    s = sub.add_parser("eval", help="extraction, SKU and cost accuracy on the labelled set")
    s.add_argument("--eval-set", default=str(DEFAULT_EVAL_SET))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_eval)
    s = sub.add_parser("ui", help="launch the Streamlit app (needs the 'ui' extra)")
    s.add_argument("streamlit_args", nargs=argparse.REMAINDER)
    s.set_defaults(func=_cmd_ui)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(args.env_file)
    try:
        return int(args.func(Settings.from_env(), args) or 0)
    except (ExtractionError, ValidationError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
