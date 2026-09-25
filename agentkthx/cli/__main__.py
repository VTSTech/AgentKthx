"""Support `python -m agentkthx.cli` (was the cli.py __main__ guard pre-split)."""

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
