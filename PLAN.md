# Plan: galaxy-tool-xml-fmt

## Status

**Skeleton only.** The opinionated formatter is not yet implemented —
this file scopes the intent and lists the open questions, drawing on
`galaxy-tool-xml/docs/decisions.md` §3 (lxml-as-source-of-truth, no
serializer in tier 1) and §9 (three-tier vision).

## Design intent

A `black`-like formatter for Galaxy tool XML: one canonical formatting
per input, no user-tunable style. The opinion lives here so tiers 1
and 2 can ignore trivia. After every format pass, repeated formatting
of the output must be a no-op (idempotence).

## What we *will* preserve

- Element structure, attribute names and order (where Galaxy XML
  doesn't impose semantic order, the formatter's canonical order
  applies)
- CDATA sections (the contents of `<command>`, `<configfile>`, etc.)
- XML comments
- Element text content verbatim
- The XML encoding declaration

## What we *will* rewrite

- Indentation (canonical: 4 spaces, no tabs; configurable later if
  the community pushes back)
- Attribute quoting (canonical: double quotes; backslash-escape
  embedded literal double-quotes)
- Empty-element shorthand (canonical: `<foo/>` over `<foo></foo>`
  when the content model permits, else expanded with no text)
- Trailing whitespace
- Blank-line policy (canonical: one blank between sibling top-level
  sections, no blank inside dense leaf sequences like `<options>`)
- Order of attributes when no ordering constraint exists (canonical:
  `name` first, then alphabetical, with a small allow-list of
  semantic-first attributes per element kind)

## Open questions

- **Idempotence proof.** How do we test it? Likely: a corpus-wide
  format → re-format → diff sweep, gated under `pytest -m slow`.
  Plumbed via the parent repo's `scripts/corpus_check.py` /
  `scripts/measure.py` conventions.
- **Config surface.** `black` has very little; we probably want even
  less. Open: line width? Attribute-per-line threshold? Decide before
  v0.1.
- **Tool-XML-specific rules.** Galaxy XML has idioms a generic XML
  formatter wouldn't know about — CDATA placement in `<command>`,
  Cheetah blocks inside `<command>`, formatting around `<expand>` and
  `<macro>` tags. These deserve dedicated rules; track each in
  `docs/decisions.md` as it ships.
- **CLI ergonomics.** Mirror `black`: `--check`, `--diff`,
  `--quiet`, recursive discovery, `pyproject.toml`-based config.
- **Integration with tier 2.** Tier 2 will call this internally for
  diff display in test harnesses; expose a stable
  `format_tool_document(document) -> bytes` API for that path.

## Milestone plan

### M0 — scaffold *(in flight)*

- `pyproject.toml`, `src/galaxy_tool_xml_fmt/`, `tests/`
- `galaxy-tool-xml` declared as a dependency
- ruff / mypy / pytest configured matching the parent
- Smoke test importing the package

### M1 — format(document) returning bytes

- `format_tool_document(document) -> bytes` — minimal canonical
  indentation + attribute quoting only; no semantic reordering yet
- Idempotence test on a small fixture set
- No CLI yet

### M2 — CLI

- `galaxy-tool-xml-fmt FILE...` — write canonical formatting back
  to each file in place
- `--check` / `--diff` / `--quiet` flags
- Recursive directory discovery

### M3 — Attribute / element ordering rules

- Per-element-kind ordering policy (sourced from the typed model)
- Decisions documented in `docs/decisions.md` as each lands

### M4 — Corpus idempotence sweep

- Format every tool in `galaxy-tool-xml/docs/corpus_data/`, re-format
  the output, diff. Any non-empty diff is a bug.
- Plug into the parent repo's `scripts/measure.py` so the result is
  cited like every other §10 measurement.

## Verification (M0 acceptance)

1. `uv sync` succeeds with `galaxy-tool-xml` as a dev path-dep.
2. `uv run pytest` runs the smoke test green.
3. `uv run ruff check .` and `uv run ruff format --check .` are clean.
4. `uv run mypy src` reports no issues.
5. The package imports without side effects and `__init__.py` exposes
   nothing yet (no `__all__`, no re-exports).
