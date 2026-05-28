"""The format pipeline entry point.

``format_tool_document`` accepts a ``ToolDocument`` from the tier-1
``galaxy-tool-xml`` library, runs every registered rule against the
document's mutable lxml tree, and returns canonical-form bytes. In this
round no rules ship, so the call is an identity transformation: the
output re-parses to a tree structurally equivalent to the input.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Side-effect imports: each rule module registers its rule class with the
# global registry on import (``@register`` runs at module load). Importing
# them here pins ``all_rules()``'s membership to the set declared by this
# pipeline.
from galaxy_tool_xml_fmt import (  # noqa: F401
    rule_blank_line,
    rule_empty_element,
    rule_indent,
    rule_param_attr_order,
    rule_tool_attr_order,
)
from galaxy_tool_xml_fmt.edits import apply_edits
from galaxy_tool_xml_fmt.rules import all_rules
from galaxy_tool_xml_fmt.serializer import to_bytes

if TYPE_CHECKING:
    from galaxy_tool_xml.document import ToolDocument


def format_tool_document(document: ToolDocument) -> bytes:
    tree = document.tree
    for rule_cls in all_rules():
        apply_edits(rule_cls().apply(tree))
    return to_bytes(tree)
