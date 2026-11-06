## zap
- main: main
- prod: none
- test: uv run ruff check . && uv run ruff format --check . && uv run pytest -q
- pr: never
- delete-branch: yes
- protected: main
