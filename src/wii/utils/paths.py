"""Repository and data-directory path resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    """Walk up from this file until a directory containing pyproject.toml is found."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"Could not locate repo root (pyproject.toml) starting from {here}")


CONFIG_DIR = Path(__file__).resolve().parent.parent / "taxonomy" / "config"


@dataclass(frozen=True)
class DataPaths:
    """Resolved data directory layout for a single pipeline run."""

    root: Path
    raw: Path
    interim: Path
    processed: Path
    metadata: Path

    @classmethod
    def from_root(cls, root: Path, raw_dir: Path | None = None) -> DataPaths:
        root = Path(root)
        return cls(
            root=root,
            raw=Path(raw_dir) if raw_dir is not None else root / "raw",
            interim=root / "interim",
            processed=root / "processed",
            metadata=root / "metadata",
        )

    def ensure_dirs(self) -> None:
        for d in (self.raw, self.interim, self.processed, self.metadata):
            d.mkdir(parents=True, exist_ok=True)
