# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project

`galaxy-tool-xml-fmt` is tier 3 of the Galaxy tool refactoring
framework. Tier 1 (`galaxy-tool-xml`) parses, validates, and exposes
typed views. Tier 2 (`galaxy-tool-xml-codemod`) performs structural
refactors. This package is the **only** component in the architecture
that writes Galaxy tool XML to disk.

The fmt tool is opinionated like `black`: a single canonical
formatting per input, no user-tunable style. The opinionated choice
goes here so the lower tiers can ignore trivia (indentation, quote
style, attribute spacing, empty-element shorthand) entirely.

## Coding standards

Hand-written code follows **dignified-python**, vendored at
`.claude/skills/dignified-python/`:

- LBYL over `try/except`. Exceptions only at the CLI error boundary
  (chained `from e`) and at third-party API boundaries with no LBYL form.
- `pathlib.Path` with explicit `encoding="utf-8"` on text I/O.
- Keyword-only arguments after the first.
- Absolute imports, no re-exports, no `__all__`.
- No import-time side effects (`@cache` for module state).

`optimized-python` (vendored at `.claude/skills/optimized-python/`) is
a secondary reference; **dignified-python governs on conflict**.

## Workflow

- **Plan-driven**: major changes get a written plan (either under
  `~/.claude/plans/` for agent state, or in `PLAN.md` for repo-scoped
  plans) before implementation.
- **Empirical claims must be backed by data.** Use the parent repo's
  corpus artifacts (`galaxy-tool-xml/docs/corpus_data/`) and its
  `scripts/measure.py` master script when answering questions about
  real-world tool XML.
- **Decisions are recorded** in `docs/decisions.md` once they land
  (mirror the parent's conventions: §10 entries cite a date and a
  reproducible measurement command).
- See `galaxy-tool-xml/docs/decisions.md` §3 (representation /
  trivia contract) and §9 (three-tier vision) for the rationale this
  tool inherits.

## Commands

- `uv sync` — install dependencies
- `uv run pytest` — run tests
- `uv run ruff check .` / `uv run ruff format .` — lint / format
- `uv run mypy src` — type-check (strict)

## Useful parent-repo references

- `galaxy-tool-xml/README.md` — tier-1 public API and the trivia
  contract this formatter respects
- `galaxy-tool-xml/docs/decisions.md` §3 (representation), §9
  (three-tier vision)
- `galaxy-tool-xml/docs/corpus_data/combined_corpus_data.json` — the
  real-world distribution of tool XML idioms the formatter must
  preserve idempotently
