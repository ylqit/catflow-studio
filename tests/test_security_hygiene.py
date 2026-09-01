from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_git_does_not_track_runtime_environment_files() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    forbidden = {
        path
        for path in tracked
        if (PROJECT_ROOT / path).exists()
        and Path(path).name.startswith(".env")
        and Path(path).name != ".env.example"
    }
    assert forbidden == set()


def test_environment_example_contains_no_secret_values() -> None:
    values = {
        line.split("=", 1)[0]: line.split("=", 1)[1].strip()
        for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.lstrip().startswith("#") and "=" in line
    }

    assert values["ARK_API_KEY"] == ""
    assert values["CAT_VIDEO_DB_PASSWORD"] == ""
