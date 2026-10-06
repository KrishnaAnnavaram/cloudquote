"""The single entry point used by the CLI and UI: text -> validated spec -> SKUs -> priced quotes."""
from __future__ import annotations

from dataclasses import dataclass

from .catalog import Catalog
from .cost import Estimate, estimate
from .extract import Extraction, Extractor, RuleBasedExtractor
from .spec import Requirement


@dataclass
class QuoteService:
    catalog: Catalog
    extractor: Extractor

    @classmethod
    def offline(cls, catalog: Catalog) -> "QuoteService":
        return cls(catalog, RuleBasedExtractor())

    def from_text(self, text: str) -> tuple[Extraction, Estimate]:
        extraction = self.extractor.extract(text)
        return extraction, estimate(self.catalog, extraction.requirement, assumptions=extraction.assumptions)

    def from_requirement(self, req: Requirement | dict | str) -> Estimate:
        if isinstance(req, str):
            req = Requirement.model_validate_json(req)
        elif isinstance(req, dict):
            req = Requirement.model_validate(req)
        return estimate(self.catalog, req)
