"""Framework tests: registry round-trip, apply_edits dispatch, format pipeline."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

import pytest
from galaxy_tool_xml.document import ToolDocument
from lxml import etree

from galaxy_tool_xml_fmt import rules
from galaxy_tool_xml_fmt.edits import Edit, NoOp, apply_edits
from galaxy_tool_xml_fmt.format import format_tool_document
from galaxy_tool_xml_fmt.rules import Rule, RuleMeta, all_rules, register

_TINY_TOOL = b"""<?xml version='1.0' encoding='UTF-8'?>
<tool id="t" name="T" version="0.1.0">
  <command><![CDATA[echo hi]]></command>
</tool>
"""


@pytest.fixture
def empty_registry() -> Iterable[None]:
    snapshot = rules._RULES.copy()
    rules._RULES.clear()
    yield
    rules._RULES[:] = snapshot


def _make_document(payload: bytes) -> ToolDocument:
    parser = etree.XMLParser(strip_cdata=False)
    root = etree.fromstring(payload, parser=parser)
    tree = etree.ElementTree(root)
    return ToolDocument(tree)


def test_register_stores_class_and_all_rules_returns_it(
    empty_registry: None,
) -> None:
    @register
    class _Probe(Rule):
        meta: ClassVar[RuleMeta] = RuleMeta(code="TEST", summary="probe", since="0.0.1")

        def apply(self, tree: etree._ElementTree) -> Iterable[Edit]:
            return ()

    assert _Probe in all_rules()


def test_apply_edits_dispatches_noop_without_changing_tree() -> None:
    doc = _make_document(_TINY_TOOL)
    before = etree.tostring(doc.tree)
    apply_edits([NoOp()])
    after = etree.tostring(doc.tree)
    assert before == after


def test_format_tool_document_is_identity_with_empty_registry(
    empty_registry: None,
) -> None:
    parser = etree.XMLParser(strip_cdata=False)
    doc = _make_document(_TINY_TOOL)
    output = format_tool_document(doc)
    reparsed = etree.fromstring(output, parser=parser)
    assert etree.tostring(reparsed) == etree.tostring(doc.tree.getroot())
