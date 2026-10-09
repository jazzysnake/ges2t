"""Tests for the ADR 0012 text-assembly table (ADR 0003, ADR 0012, ADR 0014).

The batch *emitter* (argv shape, key events, error delivery) lives in
``tests/test_ydotool_emitter.py``; this file is only the single implementation of
the trailing-space table, which :func:`decide_injection` owns and which the
emitter calls unchanged. Nothing here runs ``ydotool`` or touches
``/dev/uinput``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ges2t.injectors.ydotool import (
    SkipInjection,
    TypeText,
    decide_injection,
)


def test_empty_text_decides_to_skip() -> None:
    decision = decide_injection("", trailing_space=True)
    assert isinstance(decision, SkipInjection)


@pytest.mark.parametrize("text", [" ", "   ", "\t", "\n", " \t\n ", "\u00a0"])
def test_whitespace_only_text_decides_to_skip(text: str) -> None:
    assert isinstance(decide_injection(text, trailing_space=True), SkipInjection)
    assert isinstance(decide_injection(text, trailing_space=False), SkipInjection)


def test_trailing_space_appends_exactly_one_space() -> None:
    decision = decide_injection("hello world", trailing_space=True)
    assert isinstance(decision, TypeText)
    assert decision.payload == "hello world "


def test_no_trailing_space_leaves_text_unchanged() -> None:
    decision = decide_injection("hello world", trailing_space=False)
    assert isinstance(decision, TypeText)
    assert decision.payload == "hello world"


def test_assembly_never_strips_leading_or_trailing_whitespace() -> None:
    text = "  padded  "
    decision = decide_injection(text, trailing_space=False)
    assert isinstance(decision, TypeText)
    assert decision.payload == "  padded  "


def test_assembly_never_collapses_internal_whitespace() -> None:
    text = "two   spaces\tand\ttabs"
    decision = decide_injection(text, trailing_space=False)

    assert isinstance(decision, TypeText)
    assert decision.payload == "two   spaces\tand\ttabs"


def test_assembly_never_changes_case_or_punctuation() -> None:
    text = "Mixed CASE, punctuation?!"
    decision = decide_injection(text, trailing_space=False)
    assert isinstance(decision, TypeText)
    assert decision.payload == "Mixed CASE, punctuation?!"


def test_assembly_preserves_non_ascii_text() -> None:
    text = "Gr\u00fc\u00dfe \u2013 caf\u00e9 \U0001f600"
    decision = decide_injection(text, trailing_space=False)
    assert isinstance(decision, TypeText)
    assert decision.payload == text


def test_text_already_ending_in_space_gets_a_second_one() -> None:
    # "append exactly one space", not "normalise to one space" (ADR 0012).
    decision = decide_injection("done ", trailing_space=True)
    assert isinstance(decision, TypeText)
    assert decision.payload == "done  "


def test_decision_types_are_frozen() -> None:
    decision = decide_injection("hi", trailing_space=False)
    assert isinstance(decision, TypeText)
    with pytest.raises(FrozenInstanceError):
        decision.payload = "changed"  # type: ignore[misc]
