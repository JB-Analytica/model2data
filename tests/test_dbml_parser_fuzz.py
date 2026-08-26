"""Lightweight, fixed-seed fuzz testing for the DBML parser.

No fuzzing framework dependency (this project doesn't depend on hypothesis;
see pyproject.toml) -- instead a deterministic `random.Random(seed)` drives
two invariants `parse_dbml` must hold for *any* valid-UTF-8 text file,
however malformed:

1. It never raises an unhandled exception (`test_random_garbage_never_crashes`).
2. Small, targeted corruptions of a *valid* bundled example never silently
   drop recognizable structure with a clean exit and zero warnings
   (`test_corrupted_valid_examples_warn_or_degrade_gracefully`).
"""

from __future__ import annotations

import random
import string
import zlib
from pathlib import Path

import pytest

from model2data.parse.dbml import get_parse_warnings, parse_dbml

EXAMPLES_DIR = Path("examples")
EXAMPLE_FILES = sorted(EXAMPLES_DIR.glob("*.dbml"))

# Fixed seeds: every run of this test suite mutates/generates the exact same
# fragments, so a failure is always reproducible.
_RANDOM_SEED = 20260826
_ITERATIONS_PER_STRATEGY = 100

# A grab-bag of DBML-ish and outright garbage tokens, mixed and assembled
# into random fragments below. Deliberately includes: DBML keywords,
# brackets/braces in isolation (unbalanced), all three quote styles, ref
# operators, unicode (including combining marks and RTL), control
# characters (including a literal null), and pure punctuation noise.
_TOKENS = [
    "Table",
    "table",
    "Ref",
    "Enum",
    "enum",
    "TableGroup",
    "Project",
    "Note",
    "note:",
    "indexes",
    "{",
    "}",
    "(",
    ")",
    "[",
    "]",
    '"',
    "'",
    "`",
    ">",
    "<",
    "<>",
    ".",
    ",",
    ":",
    ";",
    "pk",
    "not null",
    "unique",
    "default:",
    "ref:",
    "int",
    "varchar",
    "id",
    "\x00",
    "\t",
    "﻿",
    "café",
    "日本語",
    "́",  # combining acute accent
    "​",  # zero-width space
    "עברית",  # RTL text
    "🚀",
    "'''",
    "//",
    "=",
    "!!!",
    "\\",
    "%s%d",
    "{{ ref('x') }}",
]


def _random_fragment(rng: random.Random) -> str:
    """Assemble a random sequence of tokens/words into a multi-line blob."""
    lines = []
    for _ in range(rng.randint(1, 12)):
        piece_count = rng.randint(0, 6)
        pieces = []
        for _ in range(piece_count):
            if rng.random() < 0.6:
                pieces.append(rng.choice(_TOKENS))
            else:
                word_len = rng.randint(1, 10)
                pieces.append(
                    "".join(
                        rng.choice(string.ascii_letters + string.digits) for _ in range(word_len)
                    )
                )
        lines.append(" ".join(pieces))
    return "\n".join(lines)


def test_random_garbage_never_crashes(tmp_path):
    """`parse_dbml` must never raise on any valid-UTF-8 text content, no
    matter how syntactically nonsensical.
    """
    rng = random.Random(_RANDOM_SEED)
    failures = []

    for i in range(_ITERATIONS_PER_STRATEGY * 3):
        fragment = _random_fragment(rng)
        dbml_file = tmp_path / f"garbage_{i}.dbml"
        dbml_file.write_text(fragment, encoding="utf-8")

        try:
            tables, refs = parse_dbml(dbml_file)
        except Exception as exc:  # noqa: BLE001 -- intentionally broad: the invariant is "never raises"
            failures.append((i, fragment, repr(exc)))
            continue

        # Whatever it produced must at least be the right shape.
        assert isinstance(tables, dict)
        assert isinstance(refs, list)
        assert isinstance(get_parse_warnings(), list)

    assert not failures, (
        f"{len(failures)} random fragment(s) crashed parse_dbml instead of "
        f"degrading gracefully. First failure (index {failures[0][0]}): "
        f"{failures[0][2]}\nFragment:\n{failures[0][1]!r}"
    )


def test_empty_and_whitespace_only_files_never_crash(tmp_path):
    for content in ["", "   ", "\n\n\n", "\t\t", "\x00", "﻿", " " * 5000]:
        dbml_file = tmp_path / "edge.dbml"
        dbml_file.write_text(content, encoding="utf-8")
        tables, refs = parse_dbml(dbml_file)
        assert tables == {}
        assert refs == []


# ---------------------------------------------------------------------
# Targeted corruption of the bundled (valid) examples
# ---------------------------------------------------------------------


def _truncate_a_ref_block(text: str, rng: random.Random) -> str:
    """Cut the file off partway through a `Ref { ... }` block."""
    idx = text.find("Ref {")
    if idx == -1:
        idx = text.find("Ref ")
    if idx == -1:
        return text[: len(text) // 2]
    cut_at = idx + rng.randint(5, 40)
    return text[: min(cut_at, len(text))]


def _remove_a_closing_brace(text: str, rng: random.Random) -> str:
    positions = [i for i, ch in enumerate(text) if ch == "}"]
    if not positions:
        return text
    pos = rng.choice(positions)
    return text[:pos] + text[pos + 1 :]


def _inject_unicode_into_identifier(text: str, rng: random.Random) -> str:
    """Splice random unicode/control characters into the first identifier
    after a `Table ` keyword.
    """
    idx = text.find("Table ")
    if idx == -1:
        return text
    insert_at = idx + len("Table ") + rng.randint(0, 3)
    junk = rng.choice(["\x00", "​", "🚀", "́", "﻿", "\t\t\t"])
    return text[:insert_at] + junk + text[insert_at:]


def _duplicate_a_table_name(text: str, rng: random.Random) -> str:
    lines = text.splitlines()
    table_line_idx = [
        i for i, line in enumerate(lines) if line.strip().lower().startswith("table ")
    ]
    if not table_line_idx:
        return text
    src = rng.choice(table_line_idx)
    lines.insert(src, lines[src])
    return "\n".join(lines)


def _remove_required_keyword(text: str, rng: random.Random) -> str:
    """Strip the literal word "Table" from one table declaration, leaving
    a bare `name {` line -- something the column-definition matcher could
    plausibly misparse as a column.
    """
    idx = text.find("Table ")
    if idx == -1:
        return text
    return text[:idx] + text[idx + len("Table ") :]


def _insert_null_bytes(text: str, rng: random.Random) -> str:
    chars = list(text)
    for _ in range(rng.randint(1, 5)):
        pos = rng.randint(0, len(chars))
        chars.insert(pos, "\x00")
    return "".join(chars)


_CORRUPTION_STRATEGIES = [
    _truncate_a_ref_block,
    _remove_a_closing_brace,
    _inject_unicode_into_identifier,
    _duplicate_a_table_name,
    _remove_required_keyword,
    _insert_null_bytes,
]


@pytest.mark.parametrize("strategy", _CORRUPTION_STRATEGIES, ids=lambda fn: fn.__name__)
@pytest.mark.parametrize("example_path", EXAMPLE_FILES, ids=lambda p: p.stem)
def test_corrupted_valid_examples_warn_or_degrade_gracefully(example_path, strategy, tmp_path):
    """Apply one corruption strategy `_ITERATIONS_PER_STRATEGY` times (with
    a different random draw each time, but deterministically seeded) to a
    known-valid bundled example, and check the two invariants:

    1. Never raises.
    2. Never silently loses structure with a clean exit and zero warnings:
       if the corrupted parse produced fewer tables than the clean parse of
       the same file, `get_parse_warnings()` must be non-empty.
    """
    original_text = example_path.read_text(encoding="utf-8")

    baseline_file = tmp_path / "baseline.dbml"
    baseline_file.write_text(original_text, encoding="utf-8")
    baseline_tables, _ = parse_dbml(baseline_file)
    baseline_table_count = len(baseline_tables)

    # zlib.crc32 (not the builtin hash()) so the derived seed is stable
    # across processes/runs -- Python salts str hash() randomly per process
    # by default, which would silently make this "fixed seed" test
    # non-reproducible.
    name_digest = zlib.crc32(f"{example_path.name}:{strategy.__name__}".encode())
    rng = random.Random(_RANDOM_SEED ^ name_digest)
    failures = []
    silent_losses = []

    for i in range(_ITERATIONS_PER_STRATEGY):
        mutated = strategy(original_text, rng)
        dbml_file = tmp_path / f"mutated_{strategy.__name__}_{i}.dbml"
        dbml_file.write_text(mutated, encoding="utf-8")

        try:
            tables, refs = parse_dbml(dbml_file)
        except Exception as exc:  # noqa: BLE001 -- invariant under test is "never raises"
            failures.append((i, repr(exc)))
            continue

        warnings = get_parse_warnings()
        if len(tables) < baseline_table_count and not warnings:
            silent_losses.append((i, len(tables), baseline_table_count))

    assert not failures, (
        f"{strategy.__name__} on {example_path.name}: {len(failures)} mutation(s) "
        f"crashed parse_dbml. First: {failures[0]}"
    )
    assert not silent_losses, (
        f"{strategy.__name__} on {example_path.name}: {len(silent_losses)} mutation(s) "
        f"silently dropped table(s) (fewer tables than the clean parse) with zero "
        f"parse warnings. First: index={silent_losses[0][0]}, "
        f"got {silent_losses[0][1]} tables vs baseline {silent_losses[0][2]}, "
        f"warnings=[]"
    )
