"""The streaming :class:`~ges2t.service.DictationSession` (ADR 0011, ADR 0015).

This is the second implementation of the session seam published by
:mod:`ges2t.service`. The batch equivalent lives in
:mod:`ges2t.pipeline`; the service treats both as an opaque object with
``start()``/``partials()``/``finish()``/``cancel()`` (ADR 0010), so this module
knows nothing about the service, the composition root, or injection.

What it owns
------------
Capture, threading, and turning the engine's segment stream into partials plus a
final transcript. :meth:`StreamingDictationSession.start` starts capture and a
worker thread that hands ``audio.chunks(chunk_seconds)`` to
:meth:`ges2t.interfaces.Transcriber.stream`; the worker records every non-final
segment as a display partial and the **final** segment as the transcript.

What it deliberately does **not** own
-------------------------------------
Windowing and delta accumulation. The commit window is an engine-specific
property that already lives in the adapter (``WhistleConfig``), and the adapter's
``stream()`` applies it by driving :func:`ges2t.streaming.drive`. Accumulating
committed deltas across window boundaries, spacing them, and emitting the final
flush tail are the adapter's job, so this module imports only
:mod:`ges2t.interfaces` and neither knows nor re-implements the window engine.

Nothing is injected until release
---------------------------------
ADR 0011's hardest rule is structural here, not a convention:

* non-final segments are appended to the private partial list;
* the final segment is stored in a **separate** private field;
* :meth:`finish` reads only the final field and :meth:`partials` reads only the
  partial list, so no code path exists that feeds partial text into the
  transcript the service later injects.

Partials are display-only (the service forwards them to ``StatusUI.partial``).

Threading discipline
--------------------
One :class:`threading.Condition` (over one :class:`threading.Lock`) guards the
shared fields — the partial list, the final text, the worker error, the
done flag, and the lifecycle. The lock is **never** held across an engine call
(iterating ``transcriber.stream``) or across ``audio.stop()``, because that is
exactly how this design deadlocks. :meth:`finish` and :meth:`cancel` stop
capture and join the worker outside the lock; ``partials()`` releases the lock
while it waits.

The worker is a **daemon** thread, deliberately: a worker that wedges while the
lock is held (a lifecycle bug) must never be able to trap the process at
interpreter shutdown, because :mod:`threading` only joins non-daemon threads.
Both :meth:`finish` and :meth:`cancel` still join it on the normal path, so the
transcript is complete before either returns; the daemon flag changes only the
failure path, from "the process hangs forever" to "the process can always
exit". The join itself is unbounded on purpose (see :meth:`_stop_capture`): a
legitimately slow final flush is indistinguishable from a wedged one, so no
timeout is safe there (ADR 0011).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from enum import StrEnum

from ges2t.interfaces import AudioSource, Transcriber, TranscriptionSegment

__all__ = ["StreamingDictationSession", "StreamingSessionError"]


class StreamingSessionError(RuntimeError):
    """Misuse of the :class:`StreamingDictationSession` lifecycle.

    Raised for a duplicate :meth:`~StreamingDictationSession.start`, a
    :meth:`~StreamingDictationSession.finish`/:meth:`~StreamingDictationSession.cancel`
    before ``start()``, or a ``finish()`` after ``cancel()``. The service never
    triggers these orderings; they exist so direct misuse fails loudly instead
    of hanging.
    """


class _Lifecycle(StrEnum):
    """The session lifecycle, a closed set (ADR 0015)."""

    NEW = "new"
    RECORDING = "recording"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class StreamingDictationSession:
    """A :class:`~ges2t.service.DictationSession` over the streaming adapter.

    ``audio`` and ``transcriber`` are injected as the :mod:`ges2t.interfaces`
    Protocols, so this class never imports the microphone or a backend
    (ADR 0010) and the tests substitute the shared fakes (ADR 0014).
    ``chunk_seconds`` sizes the reads from :meth:`AudioSource.chunks`; there is
    deliberately no ``window_seconds`` parameter — the commit window belongs to
    the adapter's own configuration (ADR 0011).
    """

    def __init__(
        self,
        audio: AudioSource,
        transcriber: Transcriber,
        *,
        chunk_seconds: float,
    ) -> None:
        if chunk_seconds <= 0:
            raise ValueError(
                f"chunk_seconds must be positive, got {chunk_seconds!r}"
            )
        self._audio = audio
        self._transcriber = transcriber
        self._chunk_seconds = chunk_seconds
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._lifecycle = _Lifecycle.NEW
        self._worker: threading.Thread | None = None
        #: Advancing, consecutive-deduplicated display text. The last element is
        #: the latest partial. Read only by :meth:`partials`.
        self._partials: list[str] = []
        #: The final segment's text. Read only by :meth:`finish`; partial text
        #: must never reach this field (ADR 0011).
        self._final_text: str | None = None
        self._error: BaseException | None = None
        self._done = False

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Start capture and the worker thread; return promptly.

        Capture is started on the caller's thread (a cheap ``start`` call), but
        ``audio.chunks()`` is driven **only** from the worker, because the audio
        source does not support two consumers (ADR 0008).
        """
        with self._condition:
            if self._lifecycle is not _Lifecycle.NEW:
                raise StreamingSessionError("start() has already been called")
            self._lifecycle = _Lifecycle.RECORDING
        self._audio.start()
        # daemon=True is the process-level guard: if a future regression wedges
        # the worker while finish()/cancel() holds the lock, interpreter
        # shutdown must still be able to exit instead of joining it forever.
        worker = threading.Thread(
            target=self._run, name="ges2t-streaming-worker", daemon=True
        )
        with self._condition:
            self._worker = worker
        worker.start()

    def partials(self) -> Iterator[str]:
        """Yield display-only partial text as it advances, deduplicated.

        The same string is never yielded twice in a row. This is a blocking
        generator that ends when the session finishes or is cancelled; it shares
        no state with :meth:`finish`, which never waits for it (ADR 0011).
        """
        index = 0
        while True:
            with self._condition:
                while index >= len(self._partials) and not self._done:
                    if self._lifecycle is _Lifecycle.NEW:
                        return
                    self._condition.wait()
                if index >= len(self._partials):
                    return
                text = self._partials[index]
                index += 1
            yield text

    def finish(self) -> str:
        """Stop capture, join the worker, and return the final transcript.

        The return value is the text of the **final** segment the transcriber
        yielded, never partial text and never a value reconstructed from it
        (ADR 0011). A raising worker is re-raised here rather than being
        swallowed into a short transcript (ADR 0015). Calling ``finish()`` twice
        returns the cached text; calling it before ``start()`` or after
        ``cancel()`` raises :class:`StreamingSessionError`.
        """
        with self._condition:
            lifecycle = self._lifecycle
            if lifecycle is _Lifecycle.NEW:
                raise StreamingSessionError("finish() called before start()")
            if lifecycle is _Lifecycle.CANCELLED:
                raise StreamingSessionError("finish() called after cancel()")
            already_finished = lifecycle is _Lifecycle.FINISHED
            self._lifecycle = _Lifecycle.FINISHED
            worker = self._worker

        stop_error = self._stop_capture(worker) if not already_finished else None

        with self._condition:
            error = self._error
            final_text = self._final_text
            done = self._done
        if error is not None:
            raise error
        if stop_error is not None:
            raise stop_error
        if not already_finished and not done:
            # Defensive: join() returned, so the worker's finally block ran.
            raise StreamingSessionError("the streaming worker did not finish")
        return final_text if final_text is not None else ""

    def cancel(self) -> None:
        """Stop capture, abandon the transcript, and join the worker.

        Nothing is returned and no text survives, so cancelling mid-utterance
        types nothing (ADR 0004, ADR 0011). ``audio.stop()`` is still called, so
        the chunk iterator ends and the worker can exit. Idempotent once the
        session is no longer recording.
        """
        with self._condition:
            lifecycle = self._lifecycle
            if lifecycle is _Lifecycle.NEW:
                raise StreamingSessionError("cancel() called before start()")
            if (
                lifecycle is _Lifecycle.FINISHED
                or lifecycle is _Lifecycle.CANCELLED
            ):
                return
            self._lifecycle = _Lifecycle.CANCELLED
            worker = self._worker

        stop_error = self._stop_capture(worker)

        with self._condition:
            self._partials.clear()
            self._final_text = None
            self._error = None
            self._done = True
            self._condition.notify_all()
        if stop_error is not None:
            raise stop_error

    # -- worker -----------------------------------------------------------

    def _stop_capture(self, worker: threading.Thread | None) -> BaseException | None:
        """Stop capture and join ``worker`` without holding the lock.

        Returns the ``audio.stop()`` failure instead of raising it so the caller
        can still clear state and re-raise in a defined order. The lock is not
        held here: this is the one place a collaborator call could deadlock.
        """
        stop_error: BaseException | None = None
        try:
            self._audio.stop()
        except BaseException as exc:
            stop_error = exc
        if worker is not None:
            # Unbounded on purpose: the final flush's latency (model-dependent
            # and potentially long) is not bounded, so any timeout here could
            # truncate a valid transcript. The daemon worker is the guard if
            # the engine wedges instead of the lock.
            worker.join()
        return stop_error

    def _run(self) -> None:
        """Consume the adapter's segment stream on the worker thread."""
        try:
            for segment in self._transcriber.stream(
                self._audio.chunks(self._chunk_seconds)
            ):
                self._record(segment)
        except BaseException as exc:
            with self._condition:
                self._error = exc
        finally:
            with self._condition:
                self._done = True
                self._condition.notify_all()

    def _record(self, segment: TranscriptionSegment) -> None:
        """Store one segment in the field that matches its kind (ADR 0011)."""
        with self._condition:
            if segment.is_final:
                # The authoritative transcript: only ever the final segment.
                self._final_text = segment.text
            elif not self._partials or self._partials[-1] != segment.text:
                # Advance the display text, dropping consecutive duplicates.
                self._partials.append(segment.text)
                self._condition.notify_all()
