"""Atomic file writes: temp file in the same directory, flush, fsync, then os.replace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl


def _atomic_write(path: Path, write_fn) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp{os.getpid()}"
    try:
        with open(tmp_path, "wb") as f:
            write_fn(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    _atomic_write(path, lambda f: f.write(data))


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def write_parquet_atomic(df: pl.DataFrame, path: Path) -> None:
    """Write a Polars DataFrame to Parquet atomically (temp file + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp{os.getpid()}"
    try:
        df.write_parquet(tmp_path)
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
