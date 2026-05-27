# galaxy-tool-xml-fmt

A `black`-like opinionated formatter for Galaxy tool XML. The third
tier of a planned three-tier Galaxy refactoring architecture:

| Tier | Package | Role |
|---|---|---|
| 1 | `galaxy-tool-xml` | parse · profile-aware validate · typed view |
| 2 | `galaxy-tool-xml-codemod` | structural refactors |
| 3 | **`galaxy-tool-xml-fmt`** *(this repo)* | opinionated formatter |

## Status

**Planned — not yet implemented.** This repo holds the design intent
and the skeleton; no formatter is shipped yet. See `PLAN.md` for the
intended scope and the first milestones.

## Role in the three-tier architecture

`galaxy-tool-xml-fmt` is the **only** component that writes Galaxy
tool XML to disk. Tiers 1 and 2 hand off mutable lxml trees with
preserved trivia (CDATA, comments, attribute order, encoding);
this tier owns the trivia-loss boundary: a format pass on a touched
file will rewrite indentation / quote style / empty-element shorthand
to the project's opinion, even when the structural change was a no-op.

The design rationale lives in `galaxy-tool-xml/docs/decisions.md`
§3 (lxml-as-source-of-truth) and §9 (three-tier vision).

## Public API

To be defined. Anticipated entry point:
`format_tool(source) -> bytes` (and a CLI `galaxy-tool-xml-fmt`
that mirrors `black`'s ergonomics — `--check`, `--diff`, recursive
discovery, `pyproject.toml` config).

## Setup

```sh
uv pip install -e ../galaxy-tool-xml
uv sync
uv run pytest
```

## Coding standards

Hand-written code follows **dignified-python** (vendored at
`.claude/skills/dignified-python/`). See `CLAUDE.md`.
