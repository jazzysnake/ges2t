"""Audio capture adapters (ADR 0008).

This package holds the concrete :class:`~ges2t.interfaces.AudioSource`
implementations. v1 has exactly one: :mod:`ges2t.audio.pw_record`, a
``pw-record`` subprocess wrapper. Downstream code depends on the
``AudioSource`` Protocol, never on the class defined here (ADR 0010).
"""
