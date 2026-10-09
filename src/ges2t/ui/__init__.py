"""User-interface surfaces for ges2t (ADR 0013).

The package holds the status-display vocabulary
(:mod:`ges2t.ui.status`) and the concrete tray implementation
(:mod:`ges2t.ui.tray`). Nothing here is imported at package import time by the
service, the CLI, or the tests, so a machine with no GTK still runs every
non-tray code path (ADR 0014).
"""
