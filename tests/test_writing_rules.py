"""Keep the repository's writing rule enforced across tracked text files."""

import html
import subprocess
from pathlib import Path


def test_tracked_text_files_do_not_use_em_dashes():
    root = Path(__file__).resolve().parents[1]
    tracked = (
        subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
        .stdout.decode()
        .split("\0")
    )
    violations = []
    for name in filter(None, tracked):
        path = root / name
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        violations.extend(
            f"{name}:{number}"
            for number, line in enumerate(content.splitlines(), start=1)
            if "\u2014" in html.unescape(line)
        )
    assert not violations, "Reword prohibited punctuation in: " + ", ".join(violations)
