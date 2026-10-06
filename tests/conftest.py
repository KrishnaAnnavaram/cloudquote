import copy
import json

import pytest

from cloudquote.catalog import DEFAULT_CATALOG, Catalog, load_catalog


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return load_catalog()


@pytest.fixture
def raw_catalog() -> dict:
    """A mutable copy of the bundled catalog JSON for negative tests."""
    return copy.deepcopy(json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8")))
