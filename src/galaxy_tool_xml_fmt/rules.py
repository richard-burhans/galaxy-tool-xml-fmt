"""Rule registry and base class for the formatter.

Rules are stateless ABCs whose ``apply()`` method inspects an lxml tree and
yields ``Edit``s describing the canonical-form mutations to perform. The
pipeline (``format.format_tool_document``) instantiates each registered
rule per format call and feeds its edits to ``apply_edits``.

Versioning convention: stability for CI consumers comes from pinning
``galaxy-tool-xml-fmt==x.y.z`` in their lockfile (no ``--rules-version``
flag). ``RuleMeta.since`` / ``RuleMeta.until`` is documentary metadata
only — ``all_rules()`` returns every rule currently in source; retirement
is a code deletion that stamps ``until`` in the deletion commit for the
changelog.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from lxml import etree

    from galaxy_tool_xml_fmt.edits import Edit


@dataclass(frozen=True)
class RuleMeta:
    code: str
    summary: str
    since: str
    until: str | None = None
    cite: str | None = None
    order: int = 100


class Rule(ABC):
    meta: ClassVar[RuleMeta]

    @abstractmethod
    def apply(self, tree: etree._ElementTree) -> Iterable[Edit]: ...


_RULES: list[type[Rule]] = []


def register(cls: type[Rule]) -> type[Rule]:
    _RULES.append(cls)
    return cls


def all_rules() -> tuple[type[Rule], ...]:
    return tuple(sorted(_RULES, key=lambda cls: cls.meta.order))
