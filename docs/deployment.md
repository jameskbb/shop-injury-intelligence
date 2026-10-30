# Deployment

The app serves the committed `data/processed/` Parquet and `data/metadata/` files. Deploy the
whole reviewed repository checkout, including `src/`, `.streamlit/`, `pyproject.toml`, and
`uv.lock`. The runtime needs no raw NEISS files, external database, or API keys. Do not enable
the optional `llm` extra for serving the app.

## Streamlit Community Cloud (live)

The public app runs at <https://shop-injury-intelligence.streamlit.app/>, deployed from this
repository's `main` branch with `app.py` as the entry point and Python 3.13 chosen under
Advanced settings. Community Cloud's default Python is older and fails `requires-python`, and
changing the version later means deleting and redeploying the app. No secrets are configured.

- **Dependencies.** Community Cloud reads `uv.lock` first and installs the locked set plus this
  project, so `app.py` can import the `wii` package. Keep `uv.lock` committed and current.
- **Updates.** A push to `main` updates the running app's files within a minute or two, and the
  next page load runs the new page code. Modules already imported, the shared DuckDB database,
  and cached query results stay in memory until the process restarts, so mixed old and new code
  can fail (for example an `ImportError` for a name the push added). After any push that changes
  `src/` or `data/`, open "Manage app" on the live app, choose Reboot app, then reload. A change
  to `uv.lock` reinstalls dependencies and restarts the process on its own.
- **Data refreshes.** The monthly refresh workflow opens a pull request when CPSC publishes a new
  year. Merging that pull request is the release; run CI on its branch first, then reboot the app
  so it reads the new Parquet files.
- **Idle sleep.** An app with no visitors for 12 hours sleeps and wakes on the next visit.
- **Sharing.** Who may view the app is set in the app's Settings, under Sharing.
- **Logs.** Use "Manage app" in the lower right of the live app; tracebacks there are full,
  while visitors see a redacted message.

The rest of this guide covers self-hosting the same revision on any server.

## Build and start

Use Python 3.13 and uv. Run from the repository root:

```bash
uv sync --locked --no-dev --python 3.13
uv run --no-sync streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true
```

Set the port to the host's assigned listening port if it differs from 8501. Configure the host's
supervisor to restart a failed process and retain application logs. Install dependencies at
build time, then use the start command above without an install step on each restart.
`--locked` rejects an out-of-date lockfile; `--no-dev` excludes test and lint dependencies.
See [uv's sync documentation](https://docs.astral.sh/uv/concepts/projects/sync/).

In another terminal, check readiness:

```bash
curl --fail --silent --show-error http://127.0.0.1:8501/_stcore/health
```

Expect HTTP 200. Configure the host's health probe for the same path. This checks the Streamlit
server, so complete the browser checklist below before declaring the release healthy. The
address, port, and probe follow [Streamlit's deployment guide](https://docs.streamlit.io/deploy/tutorials/docker).

## Network and browser requirements

Terminate HTTPS at the host or reverse proxy and forward both HTTP requests and WebSocket
upgrades to Streamlit. Keep Streamlit's security protections enabled. A root-path deployment is
the simplest starting point; a subpath needs matching `server.baseUrlPath` and proxy routing.
If running multiple replicas, enable session affinity so reconnects and media requests reach the
right process. See [Streamlit configuration](https://docs.streamlit.io/develop/api-reference/configuration/config.toml)
and its [client-server architecture](https://docs.streamlit.io/develop/concepts/architecture/architecture).

The 3D body viewer requests Three.js from jsDelivr in the visitor's browser. Allow that origin
in any deployment content policy if the 3D view is required. The flat body map remains available
when the CDN or WebGL is unavailable. Check both paths on the deployed host.

## Data updates and rollback

Refresh data through the documented [refresh process](../README.md#refreshing-data), review the
generated Parquet and metadata together, update README snapshot figures and affected screenshots,
and deploy their commit as one release. The refresh pull request does not regenerate the README
or screenshot gallery automatically. Do not run the
download pipeline during app startup or overwrite files beneath a live process. The app caches
query results without a TTL and holds a process-wide DuckDB connection, so restart every serving
process after changing data. A browser refresh alone does not invalidate these caches.

Record the deployed commit, Python version, dependency installation result, probe result, and
browser smoke result in the release record. Keep the previous checkout or image available.
If the new release fails, restore that entire release and restart it, then repeat the probe and
smoke checks. Avoid mixing data from one revision with code or metadata from another.

## Browser smoke checklist

- Open the actual HTTPS URL in a fresh browser session. Verify the Overview renders without an
  exception and the Methodology page reports the expected data years and processed case count.
- Visit all nine pages. Change the year window, age scope, and tool selection; verify the charts
  update and clearing filters restores the initial view.
- In Body Map, select a tool and a body region, orbit the 3D figure, and switch to the flat map.
  Confirm region selection works in both views and the flat view remains usable without WebGL.
- Compare two tools, select tools in My Shop, search Case Explorer, and open Signals. Check that
  suppressed cells and empty selections explain themselves without exposing errors.
- At phone width, check navigation, labels, chart overflow, and the body map controls. Use the
  keyboard to reach navigation and the flat map controls.
- Reload a page and reconnect after an idle period. Confirm the session stays usable through the
  proxy. Inspect application logs and browser console/network errors, including WebSocket errors.

## Concurrency

Streamlit serves every visitor session on its own thread. The app caches one DuckDB database per
process and hands each query its own cursor, because a single DuckDB connection is not safe for
concurrent queries. `tests/app/test_connection_threads.py` guards this.
