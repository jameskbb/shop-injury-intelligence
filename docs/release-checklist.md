# Release checklist

Reviewed 2026-10-23. This is the working queue for the first public release.

## Application fixes

- [x] Replace Signals' cross-category strength tiers with descriptive ordering within each kind.
- [x] Keep the peer-volume comparison consistent between annual-average and period-total views.
- [x] Describe filtered comparison baselines accurately and remove consequence-based tier names.
- [x] Reduce year-label crowding and separate the coding-change marker from heatmap legend text.
- [x] Explain that the temporal heatmap ranks the full record, while the preceding chart uses
  the selected year range.
- [x] Remove prohibited punctuation from tracked files and guard against regressions.
- [x] Reconcile the known-issues document with the current implementation.

## Verification

- [x] Baseline default suite: 1,234 passed, 30 real-data tests excluded.
- [x] Baseline lint and formatting checks pass.
- [x] Latest-year check: 2025 is current.
- [x] Official Query Builder reconciliation: zero mismatches across 108 rows in four years.
- [x] Basic secret-pattern scan: only known fake test keys found in the current tree and history.
- [x] Separate real-data regression suite: 29 passed; the remaining previous-release snapshot
  comparison passed separately against the committed remote release.
- [x] Clean-export CI: locked installation, lint, formatting, and 1,230 default tests passed
  (30 real-data tests excluded). Final legend/entity-guard cleanup also passed its 32 focused
  checks, followed by 27 writing and public-document checks.
- [x] Inspected Overview, Signals, Comparison, Body Map, and My Shop in desktop and phone-width
  Chromium. All 14 Body Map interaction, keyboard, rendering, and blocked-CDN fallback checks pass.
  Updated the corresponding screenshot gallery. Physical devices and Safari remain unverified.
- [x] Independent High Sol review found no actionable correctness findings or code blockers.
  Hosted behavior, public visibility, physical devices, and Safari remain outside that review.

## Publication and hosting

- [x] Document deployment, health checks, restart behavior, and rollback.
- [x] Publish the clean release lineage without older internal history or backup tags.
- [x] Run hosted CI on the published commits; CI passes on `main`.
- [x] Deploy to Streamlit Community Cloud from `main` (Python 3.13, `app.py`).
- [ ] Set the app's sharing to public, then verify the live URL, all nine pages, filters, and
  browser assets with the smoke checklist in `deployment.md`.
- [x] Link the live URL from the README; set the repository description.
- [ ] Set the repository homepage to the live URL and add topics (needs an owner-scoped token).
- [ ] Enable default-branch protection and required CI once available for the published repository.
- [ ] Confirm the refresh workflow's permission to open pull requests; the successful scheduled
  current-data check does not exercise a new-year refresh and PR creation.
- [ ] Update README snapshot figures and affected screenshots when merging future data-refresh
  pull requests; this is currently a manual review step.
- [ ] Tag the release after the exact published commit and hosted app pass their checks.

The repository is public and the app is deployed. Public sharing of the app, branch protection,
and the release tag remain owner actions.
