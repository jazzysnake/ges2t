"""Incremental typing of the committed streaming prefix (ADR 0011, amended).

During a streaming dictation the engine commits the words two consecutive passes
agree on. Ticket 32 measured that cadence on real audio: a median of **17
characters per commit**, one commit per **1.00 s** of source audio (p90 2.50 s,
max 4.00 s), with the uncommitted ``pending`` tail (median 18 characters)
persisting about **1.00 s**. So the words are available early enough to type
them as they are committed instead of waiting for release, and each commit is
cheap (ticket 23 measured ~0.38 ms/char, so a median commit is ~6 ms of typing).

What this module is
-------------------
:class:`CommittedTyper` holds a **typed watermark**: a position in the
cumulative, append-only ``LiveText.committed`` string (ticket 33 made that value
cumulative). It is deliberately *not* a sum of deltas, because the same committed
string can be reported twice -- the session dedups consecutive partials on
``display`` -- and must not be typed twice. On every committed value it types
only the suffix beyond the watermark. At :meth:`CommittedTyper.finish` it
reconciles against the **final transcript**, which is where the last collapsed
advance is recovered: ``partials()`` alone never yields a pass whose display did
not change, so a value that advanced ``committed`` while ``pending`` shrank is
only visible in the final segment (ticket 33's ``002-review.md`` §3).

The trailing space is a whole-utterance rule
--------------------------------------------
ADR 0012 appends exactly one ASCII space to the utterance, and nothing for a
whitespace-only utterance. The engine's committed deltas already carry their own
leading separators (real deltas look like ``'Good morning.'``, ``' Last'``,
``' night storm ...'``), so typing each delta through the ordinary
trailing-space path would insert a space per commit. This module types every
committed delta **raw** and applies ADR 0012 once, at the end: it assembles the
final document with :func:`~ges2t.injectors.ydotool.decide_injection`, the one
implementation of that rule, and types only the part beyond the watermark. The
result is byte-identical to what the batch path injects.

Threading and the recording loop
--------------------------------
``type_committed`` runs on the service's partials-forwarding thread and
``finish`` on its finish worker; the two can overlap because the service's stop
path does not join the forwarder. One lock serialises them and is held across the
actual typing call, so the watermark cannot advance past text that has not been
emitted yet and two callers cannot both claim the same suffix. The capture loop
never touches this lock, so a slow or wedged injector cannot stall capture; the
service still bounds shutdown by joining its workers with a timeout.
"""

from __future__ import annotations

import threading

from ges2t.injectors.ydotool import TypeText, decide_injection
from ges2t.interfaces import TextTyper

__all__ = ["CommittedTyper", "IncrementalTypingError"]


class IncrementalTypingError(RuntimeError):
    """The committed prefix did not extend the already-typed text.

    ``LiveText.committed`` is append-only (ticket 33), so a value that is not an
    extension of the typed watermark means the invariant is broken. Typing
    cannot continue without a backspace, which the design forbids, so the typer
    stops and reports instead of corrupting the document.
    """


class CommittedTyper:
    """The :class:`~ges2t.interfaces.LiveTyper` over a raw text typer.

    ``typer`` is the :class:`~ges2t.interfaces.TextTyper` primitive that types an
    exact payload; ``trailing_space`` is ``ui.trailing_space`` (ADR 0012) and is
    applied once at :meth:`finish`, never per commit.

    A ``RuntimeError`` (not a bare ``Exception``) is raised for the only
    programming-error outcome, a non-append-only committed value; delivery
    failures from the underlying typer are latched and re-raised by
    :meth:`finish`, so a mid-utterance injection failure still reaches the
    service's error path instead of being swallowed by the display thread.
    """

    def __init__(self, typer: TextTyper, *, trailing_space: bool) -> None:
        self._typer = typer
        self._trailing_space = trailing_space
        self._lock = threading.Lock()
        self._typed = ""
        self._finished = False
        self._error: Exception | None = None

    @property
    def typed(self) -> str:
        """The exact document typed so far: raw committed text plus the final space."""
        with self._lock:
            return self._typed

    def type_committed(self, committed: str) -> None:
        """Type the suffix of ``committed`` beyond the watermark.

        Never raises: a delivery failure is latched and surfaced by
        :meth:`finish` so the display thread cannot swallow it. After
        :meth:`finish` (or a latched failure) this is a no-op.
        """
        with self._lock:
            if self._finished or self._error is not None:
                return
            self._extend(committed)

    def finish(self, final_text: str) -> None:
        """Reconcile against the final transcript, apply ADR 0012 once, and stop.

        ``final_text`` is the whole transcript; it recovers any advance the
        deduplicated ``partials()`` sequence skipped. Assembles the final
        document with :func:`decide_injection` (so the trailing-space rule has
        one implementation) and types only the suffix beyond the watermark, then
        raises the first latched typing error if there was one. A second call
        after a successful one is a no-op; a call after a latched failure still
        raises it.
        """
        with self._lock:
            if self._error is not None:
                raise self._error
            if self._finished:
                return
            self._finished = True
            decision = decide_injection(
                final_text, trailing_space=self._trailing_space
            )
            target = decision.payload if isinstance(decision, TypeText) else ""
            self._extend(target)
            if self._error is not None:
                raise self._error

    def _extend(self, target: str) -> None:
        """Type ``target`` from the watermark and advance it; latch any failure."""
        if not target.startswith(self._typed):
            # The final document is not an extension of the typed prefix. For a
            # whitespace-only utterance the target is ""; anything typed before
            # it is unreachable in a real run (committed is a prefix of the
            # transcript), so this is the same invariant violation as above.
            self._error = self._non_prefix_error()
            return
        delta = target[len(self._typed) :]
        if not delta:
            return
        try:
            self._typer.type_text(delta)
        except Exception as exc:
            self._error = exc
            return
        self._typed = target

    @staticmethod
    def _non_prefix_error() -> IncrementalTypingError:
        return IncrementalTypingError(
            "the committed text is not append-only: a value that is not an "
            "extension of the already-typed text arrived, so incremental typing "
            "stopped rather than corrupt the document with a backspace"
        )
