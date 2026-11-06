# Workshop Injury Intelligence agent rules

`CLAUDE.md` and `AGENTS.md` are the same document under two names so Claude
Code and Codex follow identical rules. Change both together;
`tests/test_writing_rules.py` fails if they differ.

## Purpose

Workshop Injury Intelligence (repository `shop-injury-intelligence`) is a
public Streamlit app over the U.S. CPSC NEISS emergency-department
surveillance data. It shows how woodworking and home-workshop tools are
associated with ER-treated injuries: body region, mechanism (mined from
incident narratives), severity among ER cases, and change over time.

Core value: statistically honest, survey-weighted national estimates that
separate injury burden (how often a tool appears in estimated ER injuries)
from severity among ER cases, and never claim per-use risk, because NEISS has
no exposure denominator.

## Layout

- `app.py`: Streamlit entry point (`st.navigation`/`st.Page`).
- `src/wii/`: `data` (pipeline), `taxonomy`, `metrics` (survey-weighted
  estimates and variance), `narratives`, `analytics`, `validation`, `app`
  (pages and UI), `utils`.
- `scripts/sync_neiss.py`: data refresh CLI (uses `curl_cffi`, since cpsc.gov
  rejects plain HTTP clients). `scripts/refresh_gate.py`,
  `scripts/validate_classifications.py`: refresh gates.
  `scripts/capture_screenshots.py`: README screenshots.
- `data/processed/`, `data/metadata/`: committed curated Parquet;
  `data/raw/`, `data/interim/`, `data/reference/` are gitignored.
- `docs/`: deployment, testing, validation, release checklist, known issues.

## Install, run, test

```bash
uv sync --locked --python 3.13
uv run --locked streamlit run app.py
```

Checks (the same as CI):

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

`uv run pytest -m realdata -q` runs the real-data tier against committed
outputs. Unit tests use synthetic fixtures only and never download CPSC data.

## Constraints

- Stack: Python 3.13, uv, Streamlit, DuckDB, Parquet, Polars for
  preprocessing, pandas only as the serving layer, Plotly; the body map is a
  server-coloured SVG rendered with `st.markdown(unsafe_allow_html=True)`
  (`st.html` strips SVG).
- The app never parses raw annual files; it reads preprocessed Parquet through
  DuckDB with `st.cache_*`.
- No API key is required; the public app runs without an LLM.
- Honesty: every displayed estimate is labelled "Estimated U.S. ER-treated
  injuries", says whether it is a period total or an annual average, and
  unstable estimates are suppressed or flagged.
- Keep raw CPSC files out of git.
- Survey variance is a hand-rolled Taylor (ultimate-cluster) estimator; do not
  add `samplics`. Interactive charts use native
  `st.plotly_chart(on_select=...)`, not `streamlit-plotly-events`, and pages use
  `st.navigation`/`st.Page`, not a `pages/` folder. Do not use
  `st.components.v1.html` for the body map.

## Branch model and landing

- Land on `main` only; there is no prod branch. Contributions arrive as pull
  requests against `main`, and CI runs the checks above.
- A push to `main` updates the live Streamlit Community Cloud app. After a
  change to `src/` or `data/` lands, the maintainer reboots the app (see
  `docs/deployment.md`); a `uv.lock` change restarts it on its own.
- The monthly refresh workflow opens a `data-refresh/neiss-<year>` pull
  request; run CI on its branch before merging.
- Commit messages carry no AI attribution trailers.

## Never do

- Never write an em dash anywhere (docs, code, comments, UI text, commit
  messages). Reword with a colon, semicolon, comma, parentheses or a new
  sentence; do not substitute another dash. `tests/test_writing_rules.py`
  enforces it.
- Never force-push or rewrite history, and never change the repository's
  visibility or settings; the maintainer does that by hand.
- Never commit secrets, raw data, `.planning/`, `.claude/settings.local.json`
  or `.claude/worktrees/`.

## UI changes and images

A change to a page's interface updates its captures in the same change:
re-shoot with `uv run --with playwright==1.62.0 python scripts/capture_screenshots.py`
and rewrite any README sentence describing that screen. Re-render the title
card (`docs/images/readme-title-shop-injury-intelligence.png`, source
`docs/src/title-card.html`) with
`uv run --with playwright==1.62.0 python docs/src/render_title_card.py`
whenever the product's look or the committed data changes; its bars quote
real annual-average estimates (query in the renderer's docstring).
