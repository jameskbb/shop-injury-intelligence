"""DuckDB connection helper: registers one view per Parquet file under a processed directory
(D-01). Nothing outside wii.metrics may sum `weight` -- this module only wires up read access to
the committed curated Parquet, it never computes a weighted total itself.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from wii.utils.paths import repo_root


class MissingTableError(Exception):
    """Raised when a query needs a view that has no matching Parquet file registered."""


def connect(processed_dir: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection with one view per `*.parquet` file in `processed_dir`
    (view name = file stem). Defaults to `repo_root()/data/processed`. A view for a table that
    does not exist yet (e.g. `narrative_enrichment`) is simply absent; code that needs it should
    call `require_view` to raise a clear `MissingTableError` naming it.
    """
    if processed_dir is None:
        processed_dir = repo_root() / "data" / "processed"
    processed_dir = Path(processed_dir)

    con = duckdb.connect(":memory:")
    for path in sorted(processed_dir.glob("*.parquet")):
        view_name = path.stem
        # DuckDB's CREATE VIEW cannot be a prepared statement, so `?` binding isn't available
        # here; `path` comes from our own glob() over a trusted directory, never external input,
        # and single quotes are escaped by doubling per DuckDB's SQL string-literal rules.
        escaped_path = str(path).replace("'", "''")
        con.execute(f"CREATE VIEW \"{view_name}\" AS SELECT * FROM read_parquet('{escaped_path}')")
    return con


def require_view(con: duckdb.DuckDBPyConnection, name: str) -> None:
    """Raise `MissingTableError` naming `name` if it is not a currently registered view/table."""
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    if name not in tables:
        raise MissingTableError(
            f"required view {name!r} is not registered (missing {name}.parquet)"
        )
