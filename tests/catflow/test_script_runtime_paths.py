from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(params=["powershell.exe", "pwsh.exe"])
def powershell_executable(request: pytest.FixtureRequest) -> str:
    executable = shutil.which(request.param)
    if executable is None:
        pytest.skip(f"{request.param} is not installed")
    return executable


def test_powershell_runtime_paths_respect_relative_environment(
    tmp_path: Path, powershell_executable: str
) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "runtime-paths.ps1"
    command = (
        "$ErrorActionPreference='Stop'; "
        f". '{script}'; "
        "$env:CATFLOW_MEDIA_ROOT='var/media-custom'; "
        f"$paths=Get-CatFlowRuntimePaths -ProjectRoot '{tmp_path}'; "
        "$paths.MediaRoot"
    )

    completed = subprocess.run(
        [powershell_executable, "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert not completed.stderr
    assert Path(completed.stdout.strip()) == (tmp_path / "var/media-custom").resolve()


@pytest.mark.parametrize(
    ("configured", "error"),
    [
        ("../outside", "must remain inside the repository"),
        ("../repository-sibling/media", "must remain inside the repository"),
        (r"C:\outside", "must be a relative repository path"),
        (r"\\server\share\media", "must be a relative repository path"),
        (r"\outside", "must be a relative repository path"),
        ("/outside", "must be a relative repository path"),
        ("C:outside", "must be a relative repository path"),
    ],
)
def test_powershell_runtime_paths_reject_non_repository_paths(
    tmp_path: Path, powershell_executable: str, configured: str, error: str
) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "runtime-paths.ps1"
    command = (
        "$ErrorActionPreference='Stop'; "
        f". '{script}'; "
        f"$env:CATFLOW_MEDIA_ROOT='{configured}'; "
        f"Get-CatFlowRuntimePaths -ProjectRoot '{tmp_path / 'repository'}'"
    )

    completed = subprocess.run(
        [powershell_executable, "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode != 0
    assert error in completed.stderr


def test_start_script_discards_stale_worker_readiness_before_launch() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "start-local.ps1"
    ).read_text(encoding="utf-8")

    ready_path = script.index("$workerReadyFile =")
    remove_ready = script.index("Remove-Item -LiteralPath $workerReadyFile")
    launch_worker = script.index("$workerSupervisorProcess = Start-Process @workerStart")

    assert ready_path < remove_ready < launch_worker
    assert "ArgumentList = @('-m', 'catflow_worker.cli', 'supervise')" in script
    assert "workerSupervisorPid = $workerSupervisorProcess.Id" in script
    assert "ArgumentList = @('-m', 'catflow.interfaces.cli', 'serve'" in script
    assert "$env:__PYVENV_LAUNCHER__ = $venvPythonExecutable" in script
    assert "getattr(sys, '_base_executable', sys.executable)" in script


def test_stop_script_stops_supervisor_before_its_exact_worker_child() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "stop-local.ps1"
    ).read_text(encoding="utf-8")

    supervisor_stop = script.index("Stop-RecordedProcess -ProcessId $recorded.workerSupervisorPid")
    worker_stop = script.index("Stop-RecordedProcess -ProcessId $workerProcessId")
    api_stop = script.index("Stop-RecordedProcess -ProcessId $recorded.apiPid")

    assert supervisor_stop < worker_stop < api_stop
    assert "$recorded.workerPid" in script
    assert "Get-Process -Name" not in script


def test_start_script_invokes_the_serve_subcommand_before_its_port_option() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "start-local.ps1"
    ).read_text(encoding="utf-8")

    assert (
        "ArgumentList = @('-m', 'catflow.interfaces.cli', 'serve', "
        "'--port', $catflowPort.ToString())"
    ) in script


def test_start_script_waits_between_every_readiness_attempt() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "start-local.ps1"
    ).read_text(encoding="utf-8")

    readiness_loop = script[script.index("for ($attempt = 1;") : script.index("if (-not $ready)")]

    assert "} catch {}\n    Start-Sleep -Seconds 1\n}" in readiness_loop
