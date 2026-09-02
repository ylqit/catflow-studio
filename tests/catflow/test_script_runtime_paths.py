from __future__ import annotations

import subprocess
from pathlib import Path


def test_powershell_runtime_paths_respect_relative_environment(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "runtime-paths.ps1"
    command = (
        f". '{script}'; "
        "$env:CATFLOW_MEDIA_ROOT='var/media-custom'; "
        f"$paths=Get-CatFlowRuntimePaths -ProjectRoot '{tmp_path}'; "
        "$paths.MediaRoot"
    )

    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()) == (tmp_path / "var/media-custom").resolve()


def test_powershell_runtime_paths_reject_repository_escape(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "runtime-paths.ps1"
    command = (
        f". '{script}'; "
        "$env:CATFLOW_MEDIA_ROOT='../outside'; "
        f"Get-CatFlowRuntimePaths -ProjectRoot '{tmp_path}'"
    )

    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "repository" in completed.stderr


def test_start_script_discards_stale_worker_readiness_before_launch() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "start-local.ps1"
    ).read_text(encoding="utf-8")

    ready_path = script.index("$workerReadyFile =")
    remove_ready = script.index("Remove-Item -LiteralPath $workerReadyFile")
    launch_worker = script.index("$workerProcess = Start-Process @workerStart")

    assert ready_path < remove_ready < launch_worker
