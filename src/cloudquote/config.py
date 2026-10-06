"""Settings from environment variables (optionally loaded from a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Catalog, load_catalog
from .extract import Extractor, LLMExtractor, RuleBasedExtractor
from .llm import LLM, build_llm


def load_dotenv(path: str | os.PathLike[str] = ".env") -> int:
    """Set ``KEY=VALUE`` lines from ``path`` that are not already in the environment."""
    p = Path(path)
    if not p.is_file():
        return 0
    n = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value
            n += 1
    return n


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Settings:
    catalog_path: str = ""                 # empty = bundled sample catalog
    extractor: str = "rules"               # rules | llm
    llm_provider: str = "none"             # none | openai | gemini
    llm_model: str = ""
    llm_base_url: str = ""
    explain_with_llm: bool = False
    openai_api_key: str = field(default="", repr=False)
    gemini_api_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.extractor not in {"rules", "llm"}:
            raise ValueError(f"CLOUDQUOTE_EXTRACTOR must be 'rules' or 'llm', got {self.extractor!r}")
        if self.extractor == "llm" and self.llm_provider == "none":
            raise ValueError("CLOUDQUOTE_EXTRACTOR=llm needs CLOUDQUOTE_LLM_PROVIDER (openai or gemini)")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            catalog_path=_env("CLOUDQUOTE_CATALOG"),
            extractor=_env("CLOUDQUOTE_EXTRACTOR", "rules").lower(),
            llm_provider=_env("CLOUDQUOTE_LLM_PROVIDER", "none").lower(),
            llm_model=_env("CLOUDQUOTE_LLM_MODEL"),
            llm_base_url=_env("CLOUDQUOTE_LLM_BASE_URL"),
            explain_with_llm=_env("CLOUDQUOTE_EXPLAIN_WITH_LLM").lower() in {"1", "true", "yes"},
            openai_api_key=_env("OPENAI_API_KEY"),
            gemini_api_key=_env("GEMINI_API_KEY"),
        )

    def catalog(self) -> Catalog:
        return load_catalog(self.catalog_path or None)

    def llm(self) -> LLM | None:
        return build_llm(self.llm_provider, model=self.llm_model, base_url=self.llm_base_url,
                         openai_key=self.openai_api_key, gemini_key=self.gemini_api_key)

    def extractor_impl(self, llm: LLM | None = None) -> Extractor:
        if self.extractor == "llm":
            model = llm or self.llm()
            assert model is not None
            return LLMExtractor(model)
        return RuleBasedExtractor()
