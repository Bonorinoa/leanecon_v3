"""Sprint 23 Task 1 — extract Mathlib premises into the local RAG seed.

Walks a curated set of Mathlib subdirectories, parses top-level
``theorem`` / ``lemma`` / ``instance`` / ``def`` / ``abbrev`` declarations
with a regex tokenizer (no Lean toolchain dependency), and emits JSONL
entries matching the schema consumed by ``src.retrieval.mathlib_rag``.

Idempotent: when ``--merge`` is set against an existing seed, declarations
already present (by name) keep their existing entry — any hand-curated tags
or signatures survive.

Usage::

    python -m scripts.extract_mathlib_premises \\
        --output data/mathlib_rag_seed.jsonl --merge

    python -m scripts.extract_mathlib_premises \\
        --paths Mathlib/Topology/Order/Compact.lean ... \\
        --output data/mathlib_rag_seed.jsonl --merge
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from pathlib import Path

from src.retrieval.mathlib_rag import _split_name

REPO_ROOT = Path(__file__).resolve().parents[1]
MATHLIB_ROOT = REPO_ROOT / "lean_workspace" / ".lake" / "packages" / "mathlib"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "mathlib_rag_seed.jsonl"

# Curated high-yield extraction targets for the Sprint 23 failing claims
# (extreme value theorem, monotone convergence, fixed-point / contraction).
DEFAULT_TARGET_PATHS: tuple[str, ...] = (
    "Mathlib/Topology/Order/Compact.lean",
    "Mathlib/Topology/Order/MonotoneConvergence.lean",
    "Mathlib/Topology/Compactness/Compact.lean",
    "Mathlib/Topology/Compactness/CompactSpace.lean",
    "Mathlib/Topology/MetricSpace/Basic.lean",
    "Mathlib/Topology/MetricSpace/Cauchy.lean",
    "Mathlib/Topology/MetricSpace/Bounded.lean",
    "Mathlib/Topology/MetricSpace/Lipschitz.lean",
    "Mathlib/Topology/MetricSpace/Contracting.lean",
    "Mathlib/Topology/ContinuousOn.lean",
    "Mathlib/Topology/ContinuousMap/Bounded/Basic.lean",
    "Mathlib/Topology/Basic.lean",
    "Mathlib/Order/Filter/AtTopBot/Basic.lean",
    "Mathlib/Order/Filter/AtTopBot/Monotone.lean",
    "Mathlib/Order/Bounds/Basic.lean",
    "Mathlib/Order/Monotone/Basic.lean",
    "Mathlib/Order/LiminfLimsup.lean",
    "Mathlib/Analysis/Calculus/MeanValue.lean",
    "Mathlib/Analysis/SpecificLimits/Basic.lean",
    "Mathlib/Analysis/Normed/Order/UpperLower.lean",
    "Mathlib/Dynamics/FixedPoints/Basic.lean",
    "Mathlib/Dynamics/FixedPoints/Topology.lean",
)

# Sprint 23 lesson: `instance` and `abbrev` declarations rarely serve as
# proof premises and tend to dominate top-K rankings on metric/order/topology
# queries (e.g., `instMetricSpace`, `instCompleteSpace`). Skip them at extract
# time so the seed stays focused on theorems/lemmas/defs that are actually
# applicable as proof tactics.
SKIP_KEYWORDS: frozenset[str] = frozenset({"instance", "abbrev"})

# Path-segment → extra tag table. Lower-cased segments matched against the
# normalized file path. Used to seed semantic tags beyond what the decl name
# alone provides.
PATH_TAG_HINTS: dict[str, tuple[str, ...]] = {
    "topology": ("topology",),
    "metricspace": ("metric_space", "distance"),
    "compactness": ("compact",),
    "compact": ("compact", "extreme_value"),
    "monotoneconvergence": ("monotone_convergence", "convergence"),
    "atTopBot": ("filter", "at_top", "at_bot"),
    "monotone": ("monotone",),
    "fixedpoints": ("fixed_point", "contraction"),
    "lipschitz": ("lipschitz",),
    "calculus": ("calculus", "differentiable"),
    "continuouson": ("continuous_on", "continuous"),
    "bounds": ("bounded", "bound"),
    "specificlimits": ("limit", "convergence"),
    "meanvalue": ("mean_value", "calculus"),
    "liminflimsup": ("liminf", "limsup"),
}

# Decl-name token → extra tag (helps RAG match goal-state vocabulary).
NAME_TAG_HINTS: dict[str, tuple[str, ...]] = {
    "exists_isMaxOn": ("extreme_value", "maximum"),
    "exists_isMinOn": ("extreme_value", "minimum"),
    "exists_isLeast": ("extreme_value", "minimum"),
    "exists_isGreatest": ("extreme_value", "maximum"),
    "exists_isLUB": ("supremum", "least_upper_bound"),
    "exists_isGLB": ("infimum", "greatest_lower_bound"),
    "tendsto_atTop_of_monotone": ("monotone_convergence",),
    "Monotone.tendsto_atTop_atTop": ("monotone_convergence",),
    "isCompact": ("compact",),
    "Continuous": ("continuous",),
    "ContinuousOn": ("continuous_on", "continuous"),
}


_DECL_KEYWORD_RE = re.compile(
    r"^(?P<attrs>(?:@\[[^\]]+\]\s+)*)"
    r"(?:noncomputable\s+|protected\s+|private\s+|@\[[^\]]+\]\s+)*"
    r"(?P<keyword>theorem|lemma|instance|def|abbrev)\b"
    r"(?P<rest>\s.+)?$"
)
_DOCSTRING_OPEN_RE = re.compile(r"^/--\s*(.*)$")
_ATTR_LINE_RE = re.compile(r"^@\[[^\]]+\]\s*$")


def _is_blank(line: str) -> bool:
    return not line.strip()


def _is_line_comment(line: str) -> bool:
    return line.lstrip().startswith("--")


def _strip_priority_and_root(rest: str) -> str:
    """Remove ``(priority := N)`` qualifier and ``_root_.`` prefix from a name segment."""
    rest = rest.lstrip()
    rest = re.sub(r"^\(\s*priority\s*:?=[^)]+\)\s*", "", rest)
    rest = re.sub(r"^_root_\.", "", rest)
    return rest


def _extract_name(rest: str) -> str | None:
    """Pull the declaration name from the post-keyword text."""
    rest = _strip_priority_and_root(rest)
    # Allow letters, digits, underscore, dot, prime, Greek-ish unicode.
    match = re.match(r"([A-Za-z_][A-Za-z0-9_'.]*)", rest)
    if not match:
        return None
    name = match.group(1).rstrip(".")
    if not name:
        return None
    return name


def _looks_like_new_decl(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped or _is_line_comment(line):
        return False
    return bool(_DECL_KEYWORD_RE.match(stripped))


def _close_signature(buffer: str) -> tuple[str, bool]:
    """Return (signature_text, found_terminator).

    A signature ends at the first top-level ``:=``, `` where`` keyword,
    or end-of-line if the buffer is a one-liner ending with ``:= …`` style.
    """
    # Find ":=" or " where" (must be standalone keyword) — naive top-level scan.
    sig = buffer
    idx_assign = sig.find(":=")
    idx_where = sig.find(" where")
    candidates = [i for i in (idx_assign, idx_where) if i >= 0]
    if not candidates:
        return sig.strip(), False
    cut = min(candidates)
    return sig[:cut].strip(), True


def _strip_keyword_and_name(text: str, keyword: str, name: str) -> str:
    """Remove the leading ``keyword name`` (with possible priority/root) from text."""
    text = text.strip()
    if not text.startswith(keyword):
        return text
    text = text[len(keyword) :].lstrip()
    text = _strip_priority_and_root(text)
    if text.startswith(name):
        text = text[len(name) :]
    return text.lstrip()


def _normalize_signature(sig: str) -> str:
    """Collapse internal whitespace runs to single spaces."""
    return re.sub(r"\s+", " ", sig).strip()


def _path_based_tags(rel_path: str) -> list[str]:
    tags: list[str] = []
    lowered = rel_path.lower().replace("/", " ").replace(".lean", "")
    for key, extra in PATH_TAG_HINTS.items():
        if key.lower() in lowered:
            tags.extend(extra)
    return tags


def _name_based_tags(name: str) -> list[str]:
    tags: list[str] = list(_split_name(name))
    for key, extra in NAME_TAG_HINTS.items():
        if key in name:
            tags.extend(extra)
    return tags


def _build_tags(name: str, rel_path: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in _path_based_tags(rel_path) + _name_based_tags(name):
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def extract_premises_from_file(path: Path) -> list[dict]:
    """Parse a single .lean file and return premise dicts (seed schema)."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    rel_path = _relative_to_mathlib(path)
    lines = text.splitlines()

    premises: list[dict] = []
    pending_doc: str | None = None
    pending_attrs: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Track an in-progress doc-comment. We only attach `/-- ... -/` blocks
        # (declaration doc), not `/-! ... -/` (module/section doc).
        if stripped.startswith("/--"):
            doc_lines: list[str] = []
            tail = stripped[3:]
            terminated = False
            if tail.endswith("-/"):
                doc_lines.append(tail[:-2].strip())
                terminated = True
            else:
                if tail:
                    doc_lines.append(tail)
                j = i + 1
                while j < len(lines):
                    if lines[j].rstrip().endswith("-/"):
                        head = lines[j].rstrip()[:-2]
                        doc_lines.append(head.strip())
                        i = j
                        terminated = True
                        break
                    doc_lines.append(lines[j].strip())
                    j += 1
                if not terminated:
                    i = j
            pending_doc = " ".join(s for s in doc_lines if s).strip() or None
            i += 1
            continue

        # Module-doc / section-doc comments: skip entirely, do not attach.
        if stripped.startswith("/-!") or stripped.startswith("/-"):
            j = i
            while j < len(lines) and not lines[j].rstrip().endswith("-/"):
                j += 1
            i = j + 1
            pending_doc = None  # block comment between decls clears pending doc
            pending_attrs = []
            continue

        if _is_line_comment(line) or _is_blank(line):
            # Preserve pending_doc across blank line if blank line is the only
            # separator between doc and decl (Mathlib style allows this).
            if _is_blank(line):
                # Two blank lines wipe the doc. One blank line keeps it.
                pass
            i += 1
            continue

        if _ATTR_LINE_RE.match(stripped):
            pending_attrs.append(stripped)
            i += 1
            continue

        match = _DECL_KEYWORD_RE.match(stripped)
        if not match:
            # Non-decl source line — clear any stale pending state.
            pending_doc = None
            pending_attrs = []
            i += 1
            continue

        keyword = match.group("keyword")
        rest = (match.group("rest") or "").strip()
        name = _extract_name(rest)
        if not name:
            i += 1
            continue
        # Sprint 23 fix: skip noisy decl kinds (instance/abbrev) that pollute
        # top-K retrieval rankings without serving as proof premises.
        if keyword in SKIP_KEYWORDS:
            pending_doc = None
            pending_attrs = []
            i += 1
            continue

        # Accumulate the decl's full signature line(s) until we hit `:=`,
        # ` where`, or another decl/blank.
        buffer = stripped
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            nxt_stripped = nxt.strip()
            if _is_blank(nxt):
                break
            if _looks_like_new_decl(nxt):
                break
            buffer += " " + nxt_stripped
            if ":=" in nxt_stripped or re.search(r"\bwhere\b", nxt_stripped):
                j += 1
                break
            j += 1

        signature_raw, _ = _close_signature(buffer)
        signature = _strip_keyword_and_name(signature_raw, keyword, name)
        signature = _normalize_signature(signature)

        premises.append(
            {
                "name": name,
                "type_signature": signature,
                "docstring": pending_doc,
                "dependencies": [],
                "file_path": rel_path,
                "tags": _build_tags(name, rel_path),
            }
        )

        pending_doc = None
        pending_attrs = []
        i = j

    return premises


def _relative_to_mathlib(path: Path) -> str:
    """Return the ``Mathlib/...`` relative segment for a Mathlib source path."""
    try:
        rel = path.relative_to(MATHLIB_ROOT)
    except ValueError:
        # Path outside the canonical Mathlib root — return its name only.
        return path.name
    return str(rel).replace("\\", "/")


def extract_from_paths(paths: Iterable[Path]) -> list[dict]:
    """Extract premises from a list of files, in iteration order."""
    out: list[dict] = []
    for path in paths:
        out.extend(extract_premises_from_file(path))
    return out


def _read_existing_seed(seed_path: Path) -> list[dict]:
    if not seed_path.exists():
        return []
    entries: list[dict] = []
    for raw in seed_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def _merge_with_existing(existing: list[dict], extracted: list[dict]) -> list[dict]:
    """Existing entries (by name) win; extracted entries fill the gaps."""
    by_name: dict[str, dict] = {}
    for entry in existing:
        name = str(entry.get("name") or "")
        if name:
            by_name[name] = entry
    for entry in extracted:
        name = str(entry.get("name") or "")
        if name and name not in by_name:
            by_name[name] = entry
    return list(by_name.values())


def _write_seed(seed_path: Path, entries: list[dict]) -> None:
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    with seed_path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False))
            fh.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="Mathlib-relative paths to extract from. Defaults to a curated list.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination JSONL (default: data/mathlib_rag_seed.jsonl).",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Merge with the existing seed (existing entries win on name collision).",
    )
    args = parser.parse_args()

    rel_paths = args.paths or list(DEFAULT_TARGET_PATHS)
    full_paths = [MATHLIB_ROOT / rel for rel in rel_paths]
    missing = [p for p in full_paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"WARNING: skipping missing file {p}")
    full_paths = [p for p in full_paths if p.exists()]

    extracted = extract_from_paths(full_paths)
    print(f"Extracted {len(extracted)} declarations from {len(full_paths)} files.")

    if args.merge:
        existing = _read_existing_seed(args.output)
        merged = _merge_with_existing(existing, extracted)
        print(
            f"Merged: {len(existing)} existing + {len(extracted)} extracted "
            f"-> {len(merged)} total entries."
        )
        _write_seed(args.output, merged)
    else:
        _write_seed(args.output, extracted)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
