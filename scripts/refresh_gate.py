#!/usr/bin/env python3
"""Thin CLI shim over wii.data.refresh_gate.main."""

from wii.data.refresh_gate import main

if __name__ == "__main__":
    raise SystemExit(main())
