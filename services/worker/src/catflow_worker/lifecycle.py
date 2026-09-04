from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

LOGGER = logging.getLogger(__name__)
_RESTART_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)


def restart_delay_seconds(restart_count: int) -> float:
    if restart_count < 1:
        raise ValueError("restart_count must be positive")
    return _RESTART_DELAYS[min(restart_count - 1, len(_RESTART_DELAYS) - 1)]


def _timestamp(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat().replace("+00:00", "Z")


def _write_json_atomically(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


class WorkerHeartbeat:
    """Maintain a fresh process-health document independently of job execution."""

    def __init__(
        self,
        path: Path,
        *,
        worker_id: str,
        interval_seconds: float = 5.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._path = path
        self._worker_id = worker_id
        self._interval_seconds = interval_seconds
        self._started_at = _timestamp()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        self._write()
        self._thread = threading.Thread(
            target=self._run,
            name="catflow-worker-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._interval_seconds * 2))
        self._path.unlink(missing_ok=True)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._write()
            except OSError:
                LOGGER.exception("worker_heartbeat_write_failed")

    def _write(self) -> None:
        _write_json_atomically(
            self._path,
            {
                "schemaVersion": 2,
                "pid": os.getpid(),
                "workerId": self._worker_id,
                "provider": "ark",
                "startedAt": self._started_at,
                "heartbeatAt": _timestamp(),
            },
        )


class SupervisedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


class WorkerSupervisor:
    """Restart the local worker process without owning or mutating business jobs."""

    def __init__(
        self,
        state_file: Path,
        *,
        process_factory: Callable[[], SupervisedProcess],
        sleep: Callable[[float], None] = time.sleep,
        monitor_interval_seconds: float = 0.5,
        stable_seconds: float = 600.0,
    ) -> None:
        if monitor_interval_seconds <= 0 or stable_seconds <= 0:
            raise ValueError("supervisor timing values must be positive")
        self._state_file = state_file
        self._process_factory = process_factory
        self._sleep = sleep
        self._monitor_interval_seconds = monitor_interval_seconds
        self._stable_seconds = stable_seconds

    def run(self, stop: threading.Event) -> None:
        restart_count = 0
        last_exit_at: str | None = None
        process: SupervisedProcess | None = None
        try:
            while not stop.is_set():
                self._write_state(
                    state="starting" if restart_count == 0 else "restarting",
                    restart_count=restart_count,
                    last_exit_at=last_exit_at,
                )
                process = self._process_factory()
                started_at = time.monotonic()
                self._write_state(
                    state="ready",
                    restart_count=restart_count,
                    worker_pid=process.pid,
                    last_exit_at=last_exit_at,
                )

                exit_code: int | None = None
                while not stop.is_set():
                    exit_code = process.poll()
                    if exit_code is not None:
                        break
                    stop.wait(self._monitor_interval_seconds)

                if stop.is_set():
                    self._stop_child(process)
                    process = None
                    break

                if time.monotonic() - started_at >= self._stable_seconds:
                    restart_count = 0
                restart_count += 1
                last_exit_at = _timestamp()
                delay = restart_delay_seconds(restart_count)
                next_restart = _timestamp(datetime.now(UTC) + timedelta(seconds=delay))
                LOGGER.error(
                    "worker_process_exited exit_code=%s restart_count=%s retry_in_seconds=%s",
                    exit_code,
                    restart_count,
                    delay,
                )
                self._write_state(
                    state="degraded" if restart_count >= 3 else "restarting",
                    restart_count=restart_count,
                    last_exit_at=last_exit_at,
                    next_restart_at=next_restart,
                )
                self._sleep(delay)
        finally:
            if process is not None:
                self._stop_child(process)
            self._write_state(
                state="stopped",
                restart_count=restart_count,
                last_exit_at=last_exit_at,
            )

    @staticmethod
    def _stop_child(process: SupervisedProcess) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    def _write_state(
        self,
        *,
        state: str,
        restart_count: int,
        worker_pid: int | None = None,
        last_exit_at: str | None = None,
        next_restart_at: str | None = None,
    ) -> None:
        document: dict[str, object] = {
            "schemaVersion": 1,
            "supervisorPid": os.getpid(),
            "state": state,
            "restartCount": restart_count,
        }
        if worker_pid is not None:
            document["workerPid"] = worker_pid
        if last_exit_at is not None:
            document["lastExitAt"] = last_exit_at
        if next_restart_at is not None:
            document["nextRestartAt"] = next_restart_at
        _write_json_atomically(self._state_file, document)
