"""Text-injection adapters (ADR 0003).

v1 has exactly one implementation, :class:`~ges2t.injectors.ydotool.YdotoolInjector`,
which types through the ``ydotoold`` daemon. Clipboard-paste is deliberately
absent and is **not** an automatic fallback, so a broken injector stays visible.
"""
