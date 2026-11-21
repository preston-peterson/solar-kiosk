"""Solar and battery kiosk for a Raspberry Pi."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("solar-kiosk")  # one source of truth: pyproject.toml
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+unknown"
