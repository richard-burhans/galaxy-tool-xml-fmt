"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from galaxy_tool_xml.document import ToolDocument
from lxml import etree

# ``scripts/`` isn't a package, but the corpus-runner tests want to import
# ``corpus_check`` like any other module. Add the directory once at
# collection time; the order means ``import corpus_check`` resolves to
# the project's script, not a name collision.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture
def make_doc() -> Callable[[bytes], ToolDocument]:
    """Build a ``ToolDocument`` from raw XML bytes with CDATA preserved.

    Hand-rolling this in every test file produced the same five-line
    helper eight times. The fixture returns a callable rather than a
    pre-built document because most tests build several documents per
    test from different payloads.
    """

    def _build(payload: bytes) -> ToolDocument:
        parser = etree.XMLParser(strip_cdata=False)
        root = etree.fromstring(payload, parser=parser)
        return ToolDocument(etree.ElementTree(root))

    return _build
