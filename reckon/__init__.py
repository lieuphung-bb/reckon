"""reckon — dead reckoning for an engagement.

Fix your position from a known point plus a log of the moves you made: an
append-only event log, folded into a graph, answering where you stand and what
you can already reach.

Public surface (stable): `reckon.api` for reads and validated writes,
`reckon.reference` for the phase-2 reference-layer seam. Everything else is
internal and may change.
"""

__version__ = "0.4.0"

__all__ = ["api", "reference", "__version__"]
