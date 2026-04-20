"""Module entry so `python -m ruisheng_gw` invokes main()."""

from __future__ import annotations

import sys

from ruisheng_gw.main import main

if __name__ == "__main__":
    sys.exit(main())
