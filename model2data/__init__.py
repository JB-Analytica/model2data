"""Model2Data: Generate analytics-ready datasets from DBML models."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Read the installed distribution's version rather than repeating it here.
    # The literal that used to live in this file said 0.1.1 for every release up
    # to and including 1.2.0: nothing reads `__version__`, so nothing caught it
    # drifting. Deriving it means it cannot drift again.
    __version__ = version("model2data")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0.dev0"
