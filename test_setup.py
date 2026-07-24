#!/usr/bin/env python3
import sys
print("Step 1: imports OK", flush=True)

import warnings, logging
warnings.filterwarnings("ignore", message=".*Couldn't find ffmpeg.*")
logging.getLogger("pydub.converter").setLevel(logging.ERROR)
print("Step 2: warnings filter OK", flush=True)

class WarningFilter(logging.Filter):
    NOISE_PATTERNS = ("skipping junk", "invalid mpeg audio header", "estimating duration")
    def filter(self, record):
        if record.levelno == logging.WARNING:
            msg = record.getMessage()
            return not any(p in msg for p in self.NOISE_PATTERNS)
        return True

print("Step 3: class def OK", flush=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
for handler in logging.root.handlers:
    handler.addFilter(WarningFilter())
logger = logging.getLogger(__name__)

print("Step 4: logging OK", flush=True)

import ssl, certifi
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
print("Step 5: SSL context OK", flush=True)

logger.info("Step 6: logger.info works!")
print("All done!", flush=True)
