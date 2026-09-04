from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from catflow_worker.lifecycle import WorkerHeartbeat, WorkerSupervisor, restart_delay_seconds


def test_cli_supervisor_launches_the_worker_as_the_tracked_python_process() -> None:
    cli_source = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "worker"
        / "src"
        / "catflow_worker"
        / "cli.py"
    ).read_text(encoding="utf-8")

    assert 'getattr(sys, "_base_executable", sys.executable)' in cli_source
    assert 'child_environment["__PYVENV_LAUNCHER__"] = sys.executable' in cli_source
    assert '"-m",' in cli_source
    assert '"catflow_worker.cli",' in cli_source
    assert 'Path(sys.executable).with_name' not in cli_source


class _ExitedProcess:
    pid = 101

    def poll(self) -> int | None:
        return 7

    def terminate(self) -> None:
        raise AssertionError("an exited child must not be terminated")

    def wait(self, timeout: float | None = None) -> int:
        return 7

    def kill(self) -> None:
        raise AssertionError("an exited child must not be killed")


class _RunningProcess:
    pid = 202

    def __init__(self, stop: threading.Event) -> None:
        self._stop = stop
        self.terminated = False

    def poll(self) -> int | None:
        self._stop.set()
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        raise AssertionError("a responsive child must not be killed")


def test_worker_heartbeat_is_refreshed_independently_and_removed_on_exit(tmp_path: Path) -> None:
    ready_file = tmp_path / "worker-ready.json"

    with WorkerHeartbeat(
        ready_file,
        worker_id="worker-heartbeat-test",
        interval_seconds=0.01,
    ):
        first = json.loads(ready_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 1
        second = first
        while second["heartbeatAt"] == first["heartbeatAt"] and time.monotonic() < deadline:
            time.sleep(0.01)
            second = json.loads(ready_file.read_text(encoding="utf-8"))

        assert second["schemaVersion"] == 2
        assert second["workerId"] == "worker-heartbeat-test"
        assert second["provider"] == "ark"
        assert second["pid"] > 0
        assert second["startedAt"] == first["startedAt"]
        assert second["heartbeatAt"] != first["heartbeatAt"]

    assert not ready_file.exists()


def test_supervisor_restarts_a_crashed_worker_without_changing_business_state(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "worker-supervisor.json"
    stop = threading.Event()
    running = _RunningProcess(stop)
    children = [_ExitedProcess(), running]
    sleeps: list[float] = []

    supervisor = WorkerSupervisor(
        state_file,
        process_factory=lambda: children.pop(0),
        sleep=lambda seconds: sleeps.append(seconds),
        monitor_interval_seconds=0.01,
    )
    supervisor.run(stop)

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["schemaVersion"] == 1
    assert state["state"] == "stopped"
    assert state["restartCount"] == 1
    assert "workerPid" not in state
    assert running.terminated is True
    assert sleeps == [1.0]


def test_supervisor_restart_backoff_is_bounded() -> None:
    assert [restart_delay_seconds(count) for count in range(1, 8)] == [
        1.0,
        2.0,
        5.0,
        10.0,
        30.0,
        30.0,
        30.0,
    ]
