"""Degraded-state signal for analysis.

Raised whenever agent-mode analysis cannot safely proceed - missing
configuration, a missing optional dependency, or a failed graph build. The
caller (``runner.py``) turns this into the dossier's explicit
``analysis_incomplete`` status rather than presenting a false report.
"""


class GraphUnavailableError(RuntimeError):
    """Raised when a requested graph operation cannot safely be completed."""
