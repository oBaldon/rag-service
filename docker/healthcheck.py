"""Prontidão HTTP sem dependência de curl e sem revelar corpo/segredos."""
import sys
from urllib.error import URLError
from urllib.request import urlopen

try:
    with urlopen("http://127.0.0.1:8088/health/ready", timeout=25) as response:
        sys.exit(0 if response.status == 200 else 1)
except (URLError, TimeoutError, OSError):
    sys.exit(1)
