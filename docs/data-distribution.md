# Processed Data Distribution

This records the decision to commit the curated, workshop-subset processed Parquet directly in
this public repository, the measured evidence behind that decision, the public-domain basis for
redistributing NEISS-derived data, and the raw-stays-local rule. Nothing here changes what is
displayed to users; it documents how the app's data artifacts get from CPSC's public archive into
this repository and, from there, into every clone of it.

## Method

Sizes below are measured two ways at the same point in time: a Python byte sum over every
tracked file under the directory (`sum(f.stat().st_size for f in path.rglob("*") if f.is_file())`),
which is the number this document's budget compares against, alongside the coarser `du -sh`
figure a developer would see locally (block-size rounding, so it reads slightly higher).
`tests/scripts/test_data_distribution.py` re-runs the same byte-sum check against the live
`data/processed/` tree in CI, so the budget stays enforced as the tree changes.

## Measured Sizes (2026-09-15)

| Directory | Byte sum | du -sh | Budget |
| --- | --- | --- | --- |
| `data/processed/` | 4,550,631 bytes (4.34 MB) | 4.4M | ~50 MB |
| `data/metadata/` | 58,160 bytes (0.06 MB) | 72K | (informational; not budgeted separately) |

`data/processed/` is currently at roughly 9% of the ~50 MB committed-data budget, with over 11x headroom
before the GitHub Release fallback below would be needed.

## Decision

Keep committing the curated processed Parquet directly to this git repository as long as
`data/processed/` stays under the ~50 MB budget. At the measured 4.34 MB, no change is needed.

## Public-Domain Evidence

data.gov's own catalog entry for CPSC's National Electronic Injury Surveillance System (NEISS)
lists the dataset's license as **U.S. Public Domain** (`usa.gov/publicdomain/label/1.0`) with
public access level `"public"`. That is the basis for the decision recorded here: commit the
curated workshop-subset Parquet, and keep the raw TSVs out of git. U.S. federal
government works, and data explicitly labeled public domain by the publishing agency, carry no
copyright restriction on redistribution, so the curated Parquet derived from NEISS case data can be
committed to a public repository without a license grant from CPSC.

## Raw-Stays-Local Rule

Raw annual NEISS TSV files (`data/raw/`) are never committed. `.gitignore` excludes
`data/raw/`, `data/interim/`, and `data/reference/`; `git ls-files data/raw` returns nothing.
This is a size/hygiene decision, not a redistribution restriction (the raw files are the same
public-domain data): 20 years of raw annual files are roughly 1.2 GB uncompressed, far too
large to carry in every clone when the curated, workshop-subset
Parquet already contains everything the app needs.
`tests/scripts/test_data_distribution.py` enforces this with a CI guard: it fails if any file
under `data/raw` is tracked by git.

## Refresh Path

- **Automated:** `.github/workflows/refresh-data.yml` runs `scripts/sync_neiss.py` on a monthly
  schedule (and on-demand via `workflow_dispatch`); if a newer year is available, it re-syncs,
  re-validates, and opens a pull request with the regenerated `data/processed/` and
  `data/metadata/` (never a direct push to `main`).
- **Local:** a developer can run `uv run python scripts/sync_neiss.py --years <window>` directly
  to regenerate the same artifacts on their own machine.

## Fallback Procedure (Not Currently Needed)

If a future refresh would push `data/processed/` over the ~50 MB budget, switch to **not**
committing the Parquet directly: attach the built Parquet files to a GitHub Release instead, and
document a fetch step (e.g. a small script or README instruction that downloads the Release
asset into `data/processed/` before `streamlit run app.py`). This fallback is documented here so
the switch is a known, deliberate step rather than an emergency decision. It is not exercised
now, since the measured size (4.34 MB) leaves more than 11x headroom under the budget.
