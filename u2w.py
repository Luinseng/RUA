#!/usr/bin/env python3
"""Entry point: python u2w.py <command> ...  (see README.md)."""

import sys

from unity2web.cli import main

if __name__ == "__main__":
    sys.exit(main())
