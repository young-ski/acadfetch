"""Package-wide constants and version."""

__version__ = "0.1.1"

USER_AGENT = f"acadfetch/{__version__} (+https://github.com/young-ski/acadfetch)"

DEFAULT_CONCURRENCY = 3
DEFAULT_RATE = 2.0  # requests per second, global
DEFAULT_RETRIES = 4
# A 403 that came from a WAF/load-balancer block page clears on its own; a 403
# from the API's own permission check does not. Cap the former low so a really
# forbidden file still fails fast.
WAF_403_RETRIES = 2
DEFAULT_TIMEOUT = 60.0
CHUNK = 1 << 16
