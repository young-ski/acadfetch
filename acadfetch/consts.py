"""Package-wide constants and version."""

__version__ = "0.1.0"

USER_AGENT = f"acadfetch/{__version__} (+https://github.com/young-ski/acadfetch)"

DEFAULT_CONCURRENCY = 3
DEFAULT_RATE = 2.0  # requests per second, global
DEFAULT_RETRIES = 4
DEFAULT_TIMEOUT = 60.0
CHUNK = 1 << 16
