"""Container healthcheck: the main loop must have touched the heartbeat file recently."""
import os
import sys
import tempfile
import time
from pathlib import Path

heartbeat = Path(
    os.getenv("HDSENTINEL_HEARTBEAT_FILE")
    or Path(tempfile.gettempdir()).joinpath("hdsentinel.heartbeat")
)

try:
    interval = int(os.getenv("HDSENTINEL_INTERVAL", "600"))
except ValueError:
    interval = 600

max_age = 3 * max(interval, 10) + 60

try:
    age = time.time() - heartbeat.stat().st_mtime
except OSError:
    sys.exit(1)

sys.exit(0 if age <= max_age else 1)
