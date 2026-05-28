#!/usr/bin/env python3
"""Sweep public Galaxy tool repositories through the formatter pipeline.

A maintainer QA tool. It runs the formatter over every tool in the corpus
listed in ``corpus_sources.json`` — restricted to tools that validate under
the latest vendored profile (``26.1``) — and checks two invariants on each:

* the formatter must not **crash** on tool input;
* re-formatting the formatter's output must yield identical bytes
  (**idempotence**: ``format(format(x)) == format(x)``).

Each distinct violation is retained under ``tests/data/regressions/`` as a
permanent regression fixture; the failure signature is the sorted set of
rule codes that emitted edits on the second pass (which identifies the
non-idempotent rule), or for a crash the exception type + deepest frame.

The script also collects **per-rule trigger statistics** — how many tools
each rule emitted edits for, and the total number of edits each rule
emitted — and writes a summary to ``docs/corpus_format_stats.md``. The
stats write is skipped for partial sweeps (``--limit`` or ``--repo``).

Mirrors the design of ``galaxy-tool-xml/scripts/corpus_check.py``: same
corpus-sources walker, same fixture-retention + provenance pattern, same
``--repo`` / ``--limit`` / ``--no-stats`` switches. The two scripts answer
different questions on the same corpus, deliberately.

Usage::

    uv run python scripts/corpus_check.py [--repo NAME] [--limit N]
        [--no-stats] [--profile VERSION]

Corpus repositories are shallow-cloned into the gitignored ``corpus/``
directory on first run and reused on later runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import traceback
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path

from galaxy_tool_xml.binding import load_tool, parse_tool, validate_tool
from galaxy_tool_xml.document import ToolDocument
from lxml import etree

# Importing ``format`` triggers @register on every rule module.
from galaxy_tool_xml_fmt import format as _format_pipeline  # noqa: F401
from galaxy_tool_xml_fmt.edits import apply_edits
from galaxy_tool_xml_fmt.rules import all_rules
from galaxy_tool_xml_fmt.serializer import to_bytes

logger = logging.getLogger("corpus_check")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CORPUS_ROOT = _REPO_ROOT / "corpus"
_CORPUS_SOURCES_FILE = _REPO_ROOT / "corpus_sources.json"
_REGRESSIONS = _REPO_ROOT / "tests" / "data" / "regressions"
_STATS_FILE = _REPO_ROOT / "docs" / "corpus_format_stats.md"
_DEFAULT_PROFILE = "26.1"


@dataclass
class _FormatOutcome:
    """One tool's outcome through the format pipeline.

    ``pass1_edits`` and ``pass2_edits`` carry per-rule edit counts for the
    first format pass (raw input → canonical) and the idempotence pass
    (canonical → canonical-again). A non-empty ``pass2_edits`` is the
    diagnostic signal: a rule that keeps wanting to make edits on its own
    output is non-idempotent.
    """

    pass1_edits: Counter[str]
    pass1_bytes: bytes
    pass2_edits: Counter[str]
    pass2_bytes: bytes


@dataclass
class _SweepState:
    """Mutable bookkeeping for one ``main`` invocation.

    Counters split per pass so the stats summary can report both "rules
    that fired on real-world input" (pass 1) and "rules that violated
    idempotence" (pass 2). ``signatures`` dedups failures across the
    sweep so we retain one fixture per signature, not one per occurrence.
    """

    parsed: int = 0
    non_tool: int = 0
    unparseable: int = 0
    validated: int = 0
    formatted_ok: int = 0
    idempotent: int = 0
    non_idempotent: int = 0
    crashed: int = 0
    pass1_tools_per_rule: Counter[str] = field(default_factory=Counter)
    pass1_edits_per_rule: Counter[str] = field(default_factory=Counter)
    pass2_tools_per_rule: Counter[str] = field(default_factory=Counter)
    pass2_edits_per_rule: Counter[str] = field(default_factory=Counter)
    signatures: Counter[str] = field(default_factory=Counter)
    known_fixture_paths: set[tuple[str, str]] = field(default_factory=set)
    retained: list[tuple[str, str, Path, str, str]] = field(default_factory=list)


@cache
def _corpus_sources() -> tuple[tuple[str, str], ...]:
    """Return ``(name, url)`` pairs from ``corpus_sources.json``.

    The single source of truth for which repositories the corpus walker
    visits — adding a repo is a config edit, not a code change. Name
    doubles as the local clone directory under ``corpus/``.
    """
    raw = json.loads(_CORPUS_SOURCES_FILE.read_text(encoding="utf-8"))
    return tuple((entry["name"], entry["url"]) for entry in raw["repositories"])


def _clone_repo(name: str, url: str) -> Path | None:
    """Shallow-clone a repository into the corpus, or reuse / skip it."""
    dest = _CORPUS_ROOT / name
    if dest.exists():
        logger.info("using existing clone: %s", name)
        return dest
    logger.info("cloning %s ...", url)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning("SKIPPED %s: clone failed — %s", name, result.stderr.strip())
        return None
    return dest


def _corpus_commit(repo_dir: Path) -> str:
    """Return a repository checkout's commit SHA, or ``"unknown"``."""
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def _iter_sources(repo_filter: str | None) -> Iterable[tuple[str, Path, str]]:
    """Yield ``(name, repo_dir, commit_sha)`` for each corpus repository."""
    for name, url in _corpus_sources():
        if repo_filter is not None and name != repo_filter:
            continue
        repo_dir = _clone_repo(name, url)
        if repo_dir is None:
            continue
        yield name, repo_dir, _corpus_commit(repo_dir)


def _format_with_stats(document: ToolDocument) -> tuple[bytes, Counter[str]]:
    """Run the formatter pipeline and return ``(bytes, per-rule edit counts)``.

    Duplicates the body of ``format.format_tool_document`` so the script
    can instrument each rule's emission count without changing the
    public API. ``edit count`` here is the literal number of ``Edit``
    objects yielded by ``apply()`` — most are no-ops on already-canonical
    inputs (a ``SetText`` whose target value matches the existing one),
    so the count tracks "rule was invoked on this tool", not "rule
    changed this tool". The byte comparison in ``_exercise`` is the
    authoritative did-it-change signal.
    """
    tree = document.tree
    rule_edits: Counter[str] = Counter()
    for rule_cls in all_rules():
        edits = list(rule_cls().apply(tree))
        if edits:
            rule_edits[rule_cls.meta.code] += len(edits)
        apply_edits(edits)
    return to_bytes(tree), rule_edits


def _exercise(
    path: Path, *, profile: str
) -> tuple[str, str, str, _FormatOutcome | None]:
    """Run the formatter over one XML file and check every invariant.

    Returns ``(status, signature, detail, outcome)`` where ``status`` is
    one of:

    * ``"skip-unparseable"``: lxml's recovery parser produced no tree;
    * ``"skip-non-tool"``: the document root isn't ``<tool>``;
    * ``"skip-no-validate"``: the tool does not validate under the
      gating profile (per the memory note that the first sweep is
      restricted to tools that validate under 26.1);
    * ``"ok"``: parsed, validated, formatted, idempotent;
    * ``"non-idempotent"``: pass-2 produced different bytes than pass-1;
    * ``"crash"``: something raised.

    ``outcome`` is set whenever both passes ran (``ok`` or
    ``non-idempotent``), so the caller can roll its per-rule edit
    counters into the sweep totals.
    """
    try:
        result = parse_tool(path)
        if result.document is None:
            return "skip-unparseable", "", "", None
        if result.document.root.tag != "tool":
            return "skip-non-tool", "", "", None
        if not validate_tool(path, profile=profile).valid:
            return "skip-no-validate", "", "", None
        # First pass mutates the document tree in place, so re-parse the
        # output bytes for pass 2 rather than re-using ``result.document``.
        document_one = parse_tool(path).document
        if document_one is None:
            return "skip-unparseable", "", "", None
        pass1_bytes, pass1_edits = _format_with_stats(document_one)
        document_two = load_tool(pass1_bytes)
        pass2_bytes, pass2_edits = _format_with_stats(document_two)
        outcome = _FormatOutcome(
            pass1_edits=pass1_edits,
            pass1_bytes=pass1_bytes,
            pass2_edits=pass2_edits,
            pass2_bytes=pass2_bytes,
        )
        if pass1_bytes != pass2_bytes:
            culprits = ",".join(sorted(pass2_edits)) if pass2_edits else "no-edits"
            signature = f"non-idempotent:{culprits}"
            return (
                "non-idempotent",
                signature,
                _byte_diff_excerpt(pass1_bytes, pass2_bytes),
                outcome,
            )
        return "ok", "", "", outcome
    except Exception as exc:  # noqa: BLE001 — diagnostic sweep: every crash is a finding
        return "crash", _signature(exc), traceback.format_exc(), None


def _byte_diff_excerpt(once: bytes, twice: bytes, *, span: int = 80) -> str:
    """Excerpt around the first byte where ``once`` and ``twice`` diverge.

    The full bytes are retained in the fixture; this string is purely
    for the log line. Decoded leniently so non-utf-8 bytes don't crash
    the diagnostic.
    """
    limit = min(len(once), len(twice))
    for index in range(limit):
        if once[index] != twice[index]:
            start = max(0, index - span // 2)
            end_once = min(len(once), index + span // 2)
            end_twice = min(len(twice), index + span // 2)
            once_excerpt = once[start:end_once].decode("utf-8", errors="replace")
            twice_excerpt = twice[start:end_twice].decode("utf-8", errors="replace")
            return (
                f"first diff at byte {index}\n"
                f"  pass1: {once_excerpt!r}\n"
                f"  pass2: {twice_excerpt!r}"
            )
    return f"length differs: pass1={len(once)} pass2={len(twice)}"


def _signature(exc: BaseException) -> str:
    """A short, dedup-friendly key for a crash: exception type + deepest frame."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return type(exc).__name__
    deepest = frames[-1]
    return f"{type(exc).__name__} @ {Path(deepest.filename).name}:{deepest.lineno}"


def _imported_macro_files(path: Path) -> list[Path]:
    """Return the macro files a tool ``<import>``s, resolved beside the tool."""
    tree = etree.parse(str(path), etree.XMLParser(recover=True))
    base = path.parent
    return [
        base / element.text.strip()
        for element in tree.iter("import")
        if element.text and element.text.strip()
    ]


def _retain(path: Path, repo: str) -> Path:
    """Copy an offending tool, plus any macro files it imports, into the fixtures."""
    name = f"{repo}__{path.parent.name or path.stem}"
    dest = _REGRESSIONS / name
    suffix = 2
    while dest.exists():
        dest = _REGRESSIONS / f"{name}-{suffix}"
        suffix += 1
    dest.mkdir(parents=True)
    shutil.copy(path, dest / "tool.xml")
    for macro in _imported_macro_files(path):
        if macro.is_file():
            shutil.copy(macro, dest / macro.name)
    return dest


def _known_fixture_paths() -> set[tuple[str, str]]:
    """Return ``(repo, relative_path)`` pairs already recorded in PROVENANCE.md.

    Each failing tool retains its own fixture (the user asked to keep
    *any* tool XML causing errors, not one-per-signature), but re-running
    the sweep must not re-retain a tool that's already in the regressions
    tree. The dedup key is the source-repo + repo-relative path: stable
    across sweeps, immune to fixture-name-collision suffixes (``-2``,
    ``-3``), and unaffected if the failure signature changes after a
    rule edit.
    """
    path = _REGRESSIONS / "PROVENANCE.md"
    if not path.exists():
        return set()
    known: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("- "):
            continue
        # ``- `<fixture>` — <repo> `<rel>` @ `<commit>` — <signature>``
        # Two ` — ` separators ⇒ three fields; we want the middle one.
        parts = line.split(" — ")
        if len(parts) < 3:
            continue
        repo_field = parts[1]
        if "`" not in repo_field:
            continue
        display_name, _, rel_with_backtick = repo_field.partition("`")
        relative_path = rel_with_backtick.rstrip("`").split("` @ ", 1)[0]
        known.add((display_name.strip(), relative_path))
    return known


def _append_provenance(retained: list[tuple[str, str, Path, str, str]]) -> None:
    """Append the newly retained fixtures to the regression PROVENANCE.md."""
    path = _REGRESSIONS / "PROVENANCE.md"
    new = [
        f"- `{fixture}` — {repo} `{rel}` @ `{commit[:12]}` — {signature}"
        for fixture, repo, rel, commit, signature in retained
    ]
    if path.exists():
        existing = path.read_text(encoding="utf-8").rstrip()
        body = existing + "\n" + "\n".join(new) + "\n"
    else:
        body = "\n".join(new) + "\n"
    path.write_text(body, encoding="utf-8")


def _record_rule_stats(state: _SweepState, outcome: _FormatOutcome) -> None:
    """Roll an outcome's per-rule edit counters into the sweep totals."""
    for rule_code, count in outcome.pass1_edits.items():
        state.pass1_tools_per_rule[rule_code] += 1
        state.pass1_edits_per_rule[rule_code] += count
    for rule_code, count in outcome.pass2_edits.items():
        state.pass2_tools_per_rule[rule_code] += 1
        state.pass2_edits_per_rule[rule_code] += count


def _process_path(
    path: Path,
    *,
    display_name: str,
    repo_dir: Path,
    version: str,
    profile: str,
    state: _SweepState,
) -> bool:
    """Sweep one XML file and update ``state``; return ``True`` if it counted as a tool.

    A "counted" tool is one that parsed as ``<tool>`` and validated under
    the gating profile. Skipped files (non-XML, non-tool, didn't
    validate) do not count toward the per-repo total.
    """
    if not path.is_file():
        return False
    status, signature, detail, outcome = _exercise(path, profile=profile)
    if status == "skip-unparseable":
        state.unparseable += 1
        return False
    if status == "skip-non-tool":
        state.non_tool += 1
        return False
    state.parsed += 1
    if status == "skip-no-validate":
        return False
    state.validated += 1
    if outcome is not None:
        state.formatted_ok += 1
        _record_rule_stats(state, outcome)
    if status == "ok":
        state.idempotent += 1
        return True
    if status == "non-idempotent":
        state.non_idempotent += 1
    elif status == "crash":
        state.crashed += 1
    state.signatures[signature] += 1
    relative = path.relative_to(repo_dir)
    if (display_name, str(relative)) in state.known_fixture_paths:
        return True
    state.known_fixture_paths.add((display_name, str(relative)))
    dest = _retain(path, display_name.replace("/", "__"))
    state.retained.append((dest.name, display_name, relative, version, signature))
    logger.warning(
        "%s [%s] %s\n  %s\n  retained -> %s\n  %s",
        status.upper(),
        display_name,
        signature,
        relative,
        dest,
        detail.strip().replace("\n", "\n  "),
    )
    return True


def _format_rule_table(
    title: str,
    intro: str,
    tools_per_rule: Counter[str],
    edits_per_rule: Counter[str],
) -> list[str]:
    """Render a per-rule trigger table as markdown."""
    lines = [
        f"## {title}",
        "",
        intro,
        "",
        "| Rule | Tools touched | Edits emitted |",
        "|---|---:|---:|",
    ]
    if not tools_per_rule:
        lines.append("| _(no rule fired)_ |  |  |")
        return lines
    for rule_code in sorted(tools_per_rule):
        lines.append(
            f"| {rule_code} | {tools_per_rule[rule_code]} | "
            f"{edits_per_rule[rule_code]} |"
        )
    return lines


def _format_summary_table(state: _SweepState) -> list[str]:
    """Render the headline counts as a markdown table."""
    lines = [
        "## Sweep summary",
        "",
        "| Outcome | Tools |",
        "|---|---:|",
        f"| Parsed as `<tool>` | {state.parsed} |",
        f"| Unparseable XML (skipped) | {state.unparseable} |",
        f"| Non-tool root (skipped) | {state.non_tool} |",
        f"| Validated under gating profile | {state.validated} |",
        f"| Formatted without crashing | {state.formatted_ok} |",
        f"| **Idempotent** | **{state.idempotent}** |",
        f"| Non-idempotent | {state.non_idempotent} |",
        f"| Crashed | {state.crashed} |",
    ]
    return lines


def _format_signatures_table(signatures: Counter[str]) -> list[str]:
    """Render the dedup'd failure signatures as a markdown table."""
    lines = ["## Failure signatures", ""]
    if not signatures:
        lines.append("_(no failures)_")
        return lines
    lines.extend(
        [
            "| Signature | Occurrences |",
            "|---|---:|",
        ]
    )
    for signature, count in signatures.most_common():
        lines.append(f"| `{signature}` | {count} |")
    return lines


def _write_stats(
    *,
    profile: str,
    repos: list[tuple[str, str, int]],
    state: _SweepState,
) -> None:
    """Write the corpus-format statistics artifact to ``docs/``."""
    _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# Corpus format statistics",
        "",
        (
            f"Generated by `scripts/corpus_check.py` on "
            f"{date.today().isoformat()}, gated on validation under profile "
            f"`{profile}`. Swept {state.parsed} tool documents across "
            f"{len(repos)} repositories; "
            f"{state.validated} validated under `{profile}` and were "
            f"format-checked."
        ),
        "",
        (
            "Regenerated by every full run of `corpus_check.py` unless "
            "`--no-stats` is given; partial sweeps (`--limit` or `--repo`) "
            "do not regenerate it. Per-repo version labels make the snapshot "
            "reproducible."
        ),
        "",
        "## Repositories",
        "",
        "| Repository | Version | Tool documents |",
        "|---|---|---:|",
    ]
    for name, commit, count in sorted(repos):
        lines.append(f"| {name} | `{commit[:12]}` | {count} |")
    lines.append("")
    lines.extend(_format_summary_table(state))
    lines.append("")
    lines.extend(
        _format_rule_table(
            "Pass 1 rule triggers (raw input → canonical)",
            (
                "Per-rule counts for the first format pass. *Tools touched* "
                "is the number of validated tools where the rule emitted at "
                "least one `Edit`; *edits emitted* is the literal count of "
                "`Edit` objects yielded — many are no-ops on inputs that "
                "happen to already be canonical, so this overstates "
                '"changes actually made". Use the byte comparison '
                "(`idempotent` / `non-idempotent` rows above) for the "
                "did-it-change signal."
            ),
            state.pass1_tools_per_rule,
            state.pass1_edits_per_rule,
        )
    )
    lines.append("")
    lines.extend(
        _format_rule_table(
            "Pass 2 rule triggers (canonical → canonical, must be empty)",
            (
                "Per-rule counts for the idempotence pass. A canonical-form "
                "input should produce no further edits; any rule that fires "
                "here is the source of a non-idempotence. Edit counts on "
                "no-op-friendly rules (GTX001's per-element `SetText`) can "
                "be non-zero without breaking byte equality — the "
                "non-idempotent row above is still the authoritative count."
            ),
            state.pass2_tools_per_rule,
            state.pass2_edits_per_rule,
        )
    )
    lines.append("")
    lines.extend(_format_signatures_table(state.signatures))
    lines.append("")
    _STATS_FILE.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    """Sweep the corpus through the formatter and retain regression fixtures."""
    parser = argparse.ArgumentParser(
        description=(
            "Sweep the Galaxy tool corpus through galaxy-tool-xml-fmt and "
            "check format → re-format idempotence."
        )
    )
    parser.add_argument(
        "--repo",
        help="sweep only this repository (by name from corpus_sources.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="stop after N tools total (0 sweeps everything)",
    )
    parser.add_argument(
        "--no-stats",
        action="store_true",
        help=(
            "don't regenerate docs/corpus_format_stats.md (implicit for "
            "partial sweeps with --limit or --repo)"
        ),
    )
    parser.add_argument(
        "--profile",
        default=_DEFAULT_PROFILE,
        help=(
            "gating profile: tools that don't validate under this profile "
            f"are skipped (default: {_DEFAULT_PROFILE})"
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.repo is not None:
        known_names = {name for name, _ in _corpus_sources()}
        if args.repo not in known_names:
            known = ", ".join(sorted(known_names))
            logger.error("unknown --repo %r; known: %s", args.repo, known)
            return 1

    _CORPUS_ROOT.mkdir(parents=True, exist_ok=True)
    state = _SweepState(known_fixture_paths=_known_fixture_paths())
    repo_tool_counts: list[tuple[str, str, int]] = []
    tools = 0
    for display_name, repo_dir, version in _iter_sources(repo_filter=args.repo):
        repo_tool_count = 0
        for path in sorted(repo_dir.rglob("*.xml")):
            if args.limit and tools >= args.limit:
                break
            if not _process_path(
                path,
                display_name=display_name,
                repo_dir=repo_dir,
                version=version,
                profile=args.profile,
                state=state,
            ):
                continue
            tools += 1
            repo_tool_count += 1
            if tools % 500 == 0:
                logger.info("... %d tools", tools)
        repo_tool_counts.append((display_name, version, repo_tool_count))
        if args.limit and tools >= args.limit:
            break

    logger.info(
        "swept %d tools; %d validated@%s; %d idempotent; %d non-idempotent; %d crashed",
        tools,
        state.validated,
        args.profile,
        state.idempotent,
        state.non_idempotent,
        state.crashed,
    )
    for signature, count in state.signatures.most_common():
        logger.info("  %6d  %s", count, signature)
    if state.retained:
        _append_provenance(state.retained)
        logger.info(
            "retained %d new regression fixture(s) under %s",
            len(state.retained),
            _REGRESSIONS,
        )
    if args.no_stats:
        return 0
    if args.limit or args.repo:
        logger.info(
            "corpus stats not regenerated: partial sweep (--limit or --repo). "
            "Run the full sweep to refresh %s.",
            _STATS_FILE.relative_to(_REPO_ROOT),
        )
        return 0
    _write_stats(profile=args.profile, repos=repo_tool_counts, state=state)
    logger.info("corpus stats -> %s", _STATS_FILE.relative_to(_REPO_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
