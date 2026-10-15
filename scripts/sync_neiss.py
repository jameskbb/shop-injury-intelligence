#!/usr/bin/env python3
"""Thin CLI shim over wii.data.sync.main."""

from wii.data.sync import main

if __name__ == "__main__":
    raise SystemExit(main())
