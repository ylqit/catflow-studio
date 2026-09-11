"""Loopback-only real API/worker/media fixture; no provider network or production DB.

Run .venv/Scripts/python tests/catflow/browser_edit_fixture.py --help.
Fresh runs own and drop their UUID test database on graceful shutdown.
--resume-manifest preserves its validated existing test database on shutdown.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi import Body, HTTPException, Request
from PIL import Image, ImageDraw
from psycopg import OperationalError
from sqlalchemy import create_engine, event, make_url, select, text
from sqlalchemy.engine import Engine

from catflow.application import service as dto
from catflow.application.gateways import ProviderGatewayError
from catflow.application.provider_config import ProviderRuntime
from catflow.infrastructure.database import DatabaseSettings, create_session_factory
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import AssetRecord, JobEventRecord, JobRecord
from catflow.infrastructure.object_storage import ObjectPublisherStatus
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow.interfaces.api import AppSettings, create_app
from catflow_worker.ark_results import ArkResultLandingService
from catflow_worker.media_jobs import LocalMediaJobExecutor
from catflow_worker.media_probe import inspect_video
from catflow_worker.provider_media import ProviderMediaDownloader
from catflow_worker.provider_receipts import ReceiptJournal
from catflow_worker.runner import DurableJobWorker, ProviderPoll, ProviderSubmission
from catflow_worker.runtime_support import AssetMediaResolver, JobResultDispatcher

ROOT = Path(__file__).resolve().parents[2]


def validate_resume_receipts(jobs, journal, registered):
    """Refuse unknown-job recovery before a resumed worker can reconcile its journal."""
    for job in jobs:
        if job.status != "submission_unknown":
            continue
        facts = job.execution_json or {}
        if (
            facts.get("resultComplete")
            or job.provider_task_id
            or (job.provider_response_id and facts.get("store"))
        ):
            raise ValueError(
                f"unknown job {job.id} has recoverable facts; explicit recovery required"
            )
        for receipt in journal.read(job.id):
            reference = receipt["reference"]
            if registered.get((job.id, reference["id"])) != reference["sha256"]:
                raise ValueError(
                    f"unknown job {job.id} has an unregistered or changed receipt; "
                    "explicit recovery required; evidence preserved"
                )


class FixturePublisherRuntime:
    """Publisher capability double: the fixture gateway resolves local media directly."""

    status = ObjectPublisherStatus(
        configured=True,
        ready=True,
        backend="s3",
        endpoint_host="fixture.invalid",
        public_host="fixture.invalid",
        bucket="fixture",
        region="fixture",
        addressing_style="path",
        presign_ttl_seconds=7200,
        retention_days=1,
    )

    def check_roundtrip(self):
        # This boundary deliberately makes no network request.
        return self.status


class FixtureGateway:
    """Deterministic provider boundary with real timeline/reference preparation."""

    def __init__(self, resolver):
        self.resolver = resolver
        self.next_outcome = "normal"
        self.delay_seconds = 5.0
        self.creates = []
        self.queries = []
        self.tasks = {}

    def prepare_submission(self, *, job_id, kind, frozen_input):
        if kind == "plan_video_edit":
            self.resolver.prepare_edit_plan_frames(job_id, frozen_input)
        elif kind == "regenerate_video_segment":
            if frozen_input.get("referencePreparationJobId"):
                references = list(frozen_input["imageReferences"])
                if frozen_input.get("videoReference"):
                    references.append(frozen_input["videoReference"])
                self.resolver.resolve_paths(
                    tuple(uuid.UUID(item["assetId"]) for item in references)
                )
                return
            generation, issue = frozen_input["generationRange"], frozen_input["issueRange"]
            self.resolver.prepare_segment_media(
                job_id,
                uuid.UUID(frozen_input["baseVideoAssetId"]),
                generation["startFrame"],
                generation["endFrame"],
                issue["startFrame"],
                issue["endFrame"],
                frozen_input["durationSeconds"],
            )
        else:
            raise ValueError(f"fixture does not implement provider kind {kind}")

    def submit(self, *, job_id, kind, frozen_input):
        if kind == "plan_video_edit":
            self.creates.append({"jobId": str(job_id), "kind": kind, "outcome": "plan"})
            return ProviderSubmission(
                result={
                    "payload": {
                        "instruction": "让主体平稳向前移动",
                        "preserveContent": "保留主体和背景",
                        "startState": "主体位于画面中央",
                        "actionProcess": "缓慢向前移动",
                        "desiredEndState": "主体停在画面中央",
                        "avoidProblems": "避免跳帧",
                        "recommendedGenerationMode": "edit_existing",
                        "recommendedEndStatePolicy": "follow_instruction",
                        "recommendedReferenceRoles": ["episode_child", "episode_cat"],
                        "notes": ["离线确定性建议；本步骤不生成视频"],
                    }
                }
            )
        outcome, self.next_outcome = self.next_outcome, "normal"
        self.creates.append({"jobId": str(job_id), "kind": kind, "outcome": outcome})
        if outcome == "submission_unknown":
            return ProviderSubmission()
        if outcome == "rejection":
            raise ProviderGatewayError(
                code="fixture_rejected",
                message="测试供应商明确拒绝",
                retryable=False,
                submission_unknown=False,
            )
        task_id = f"fixture-{uuid.uuid4().hex}"
        self.tasks[task_id] = (time.monotonic() + self.delay_seconds, outcome)
        return ProviderSubmission(task_id=task_id)

    def poll(self, provider_task_id):
        self.queries.append(provider_task_id)
        ready_at, outcome = self.tasks[provider_task_id]
        if time.monotonic() < ready_at:
            return ProviderPoll(status="running", provider_status="running")
        if outcome == "result_failure":
            return ProviderPoll(
                status="failed",
                provider_status="failed",
                error={"code": "fixture_result_failure", "message": "测试结果失败"},
            )
        return ProviderPoll(
            status="succeeded",
            provider_status="succeeded",
            result={"videoUrl": "https://fixture.invalid/candidate.mp4"},
        )


class FixtureDownloader(ProviderMediaDownloader):
    """Keep real download validation; substitute only transport with synthetic bytes."""

    def __init__(self, ffmpeg, fixture_directory):
        self.ffmpeg = ffmpeg
        self.directory = fixture_directory
        self.downloads = 0
        self.seconds = 15

    def download_video(self, url, destination, *, ffprobe_path, expected_duration_seconds):
        self.seconds = expected_duration_seconds
        return super().download_video(
            url,
            destination,
            ffprobe_path=ffprobe_path,
            expected_duration_seconds=expected_duration_seconds,
        )

    def _download(self, url, destination, *, maximum_bytes):
        if url != "https://fixture.invalid/candidate.mp4":
            raise ValueError("fixture refuses unexpected provider URL")
        self.downloads += 1
        source = self.directory / f"candidate-{self.seconds}.mp4"
        if not source.exists():
            render_video(self.ffmpeg, source, self.seconds * 24, "0x285eaa", 880)
        shutil.copyfile(source, destination)


def render_video(ffmpeg, path, frames, color, tone):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=480x854:r=24",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:sample_rate=48000",
            "-t",
            f"{frames / 24:.9f}",
            "-vf",
            "drawbox=x=40+mod(t*40\\,300):y=300:w=100:h=100:color=white:t=fill",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--admin-url", default="postgresql+psycopg://postgres@127.0.0.1:55439/postgres"
    )
    parser.add_argument("--port", type=int, default=8879)
    parser.add_argument("--resume-manifest", type=Path)
    parser.add_argument(
        "--use-configured-admin",
        action="store_true",
        help="Explicitly reuse configured PG server credentials for a new test DB",
    )
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg"))
    parser.add_argument(
        "--ffprobe", default=os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")
    )
    args = parser.parse_args()

    @event.listens_for(Engine, "do_connect")
    def connect_fixture_database(dialect, connection_record, connection_args, connection_params):
        # Only connection establishment retries; never replay a transaction or provider call.
        connection_params["connect_timeout"] = 5
        for attempt in range(8):
            try:
                return dialect.connect(*connection_args, **connection_params)
            except OperationalError:
                if attempt == 7:
                    raise
                logging.warning("fixture database connection retry %s/8", attempt + 1)
                time.sleep(1)

    if not args.ffmpeg or not args.ffprobe:
        parser.error("supply --ffmpeg and --ffprobe")
    if args.use_configured_admin:
        for key, value in dotenv_values(ROOT / ".env").items():
            if value is not None and (
                key.startswith("CATFLOW_DB_") or key == "CATFLOW_DATABASE_URL"
            ):
                os.environ[key] = value
        admin_url = make_url(DatabaseSettings.from_env().url).set(database="postgres")
    else:
        admin_url = make_url(args.admin_url)
    if (
        not args.use_configured_admin and admin_url.host not in {"127.0.0.1", "localhost", "::1"}
    ) or admin_url.database != "postgres":
        parser.error("admin URL must target loopback /postgres; production URLs are forbidden")
    if args.port == 8877:
        parser.error("production port 8877 is forbidden")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    resumed = None
    if args.resume_manifest:
        manifest_path = args.resume_manifest.resolve(strict=True)
        resumed = json.loads(manifest_path.read_text(encoding="utf-8"))
        name = resumed["database"]
        if not re.fullmatch(r"catflow_studio_test_browser_[a-f0-9]{32}", name):
            parser.error("resume database must be an owned browser fixture UUID database")
        directory = (ROOT / "output" / "playwright" / name).resolve(strict=True)
        if (
            manifest_path != directory / "manifest.json"
            or Path(resumed["directory"]).resolve(strict=True) != directory
            or directory.parent != (ROOT / "output/playwright").resolve()
            or not (directory / "media").is_dir()
            or (directory / "media").resolve().parent != directory
            or not (directory / "receipts").is_dir()
            or (directory / "receipts").resolve().parent != directory
        ):
            parser.error("resume manifest/media/receipts must belong to its fixture directory")
        if resumed["baseURL"] != f"http://127.0.0.1:{args.port}":
            parser.error("resume port must match the original fixture")
        if not re.fullmatch(r"[a-f0-9]{32}", resumed["csrfToken"]):
            parser.error("invalid original fixture CSRF token")
    else:
        name = f"catflow_studio_test_browser_{uuid.uuid4().hex}"
        directory = ROOT / "output" / "playwright" / name
        directory.mkdir(parents=True)
    admin = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    engine = None
    stop, paused = threading.Event(), threading.Event()
    threads = []
    created = False
    try:
        if resumed is None:
            with admin.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
                created = True
        os.environ["CATFLOW_DATABASE_URL"] = admin_url.set(database=name).render_as_string(
            hide_password=False
        )
        if resumed is None:
            command.upgrade(Config(str(ROOT / "services/api/alembic.ini")), "head")
        engine = create_engine(
            os.environ["CATFLOW_DATABASE_URL"],
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 5,
                "keepalives": 1,
                "keepalives_idle": 10,
                "keepalives_interval": 5,
                "keepalives_count": 3,
            },
        )
        sessions = create_session_factory(engine)
        repo = PostgresStudioRepository(sessions)
        runtime = ProviderRuntime(
            provider="ark",
            planning_model="fixture",
            image_model="fixture",
            video_model="fixture",
            diagnostic_model="fixture",
            capability_revision="fixture",
            paid_calls_enabled=True,
            maximum_video_references=9,
            segment_reference_publishing_ready=True,
            api_base_url="https://fixture.invalid/api/v3",
        )
        service = dto.StudioService(repo, provider_runtime=runtime)
        historical_creates = []
        if resumed:
            draft = service.get_video_edit_draft(
                uuid.UUID(resumed["projectId"]), uuid.UUID(resumed["draftId"])
            )
            source = service.get_asset(uuid.UUID(resumed["baseVideoAssetId"]))
            if source.project_id != draft.project_id:
                raise ValueError("resumed source does not belong to the manifest project")
            with sessions() as session:
                jobs = list(session.scalars(select(JobRecord)))
                if any(
                    job.status
                    in {
                        "queued",
                        "submitting",
                        "submitted",
                        "polling",
                        "storing",
                        "cancel_requested",
                    }
                    for job in jobs
                ):
                    raise ValueError("resume requires settled jobs; refusing implicit replay")
                registered = {
                    (event.job_id, event.payload_json["receiptId"]): event.payload_json.get(
                        "receipt", {}
                    ).get("sha256")
                    for event in session.scalars(select(JobEventRecord))
                    if event.payload_json and event.payload_json.get("receiptId")
                }
                validate_resume_receipts(jobs, ReceiptJournal(directory / "receipts"), registered)
                historical_creates = [
                    {
                        "jobId": str(job.id),
                        "kind": job.kind,
                        "status": job.status,
                        "taskId": job.provider_task_id,
                        "evidence": "database submission_started_at; not a new fixture call",
                    }
                    for job in jobs
                    if job.provider_submission_started_at is not None
                ]
        store = LocalMediaStore(directory / "media")
        ffmpeg, ffprobe = Path(args.ffmpeg), Path(args.ffprobe)
        local = LocalMediaJobExecutor(sessions, store, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
        resolver = AssetMediaResolver(
            sessions, store, ffmpeg_path=ffmpeg, timeline_renderer=local, ffprobe_path=ffprobe
        )
        gateway = FixtureGateway(resolver)
        downloader = FixtureDownloader(ffmpeg, directory)
        landing = ArkResultLandingService(
            sessions,
            store,
            studio_service=service,
            downloader=downloader,
            ffprobe_path=ffprobe,
            ffmpeg_path=ffmpeg,
        )
        dispatcher = JobResultDispatcher(sessions, local=local, ark=landing, references=resolver)
        base_url = f"http://127.0.0.1:{args.port}"
        csrf = resumed["csrfToken"] if resumed else uuid.uuid4().hex
        app = create_app(
            service,
            settings=AppSettings(
                csrf_token=csrf,
                base_url=base_url,
                ark_api_key_configured=True,
                allowed_origins=(base_url,),
            ),
            media_store=store,
            spa_dist=ROOT / "apps/web/dist",
            object_publisher_runtime=FixturePublisherRuntime(),
        )
        # Place harness routes before the production SPA catch-all.
        spa = app.router.routes.pop()
        cases = []

        def seed_case(title="隔离浏览器剪辑测试"):
            project = service.create_project(
                dto.ProjectCreate(title=title, theme="本地合成测试", targetDurationSeconds=15)
            )
            key = "fixture/root.mp4"
            path = store.resolve(key)
            if not path.exists():
                render_video(ffmpeg, path, 361, "0xa34434", 440)
            facts = inspect_video(path, ffprobe, ffmpeg_path=ffmpeg)
            asset = service.register_asset(
                project.id,
                role="video",
                media_type="video",
                storage_key=key,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                byte_size=path.stat().st_size,
                metadata=facts,
            )
            for index, role in enumerate(
                ("episode_child", "episode_cat", "pair_scale", "environment", "style_board")
            ):
                image_key = f"fixture/{role}.png"
                image_path = store.resolve(image_key)
                image = Image.new("RGB", (480, 854), (40 + index * 30, 100, 160))
                ImageDraw.Draw(image).text((30, 40), role, fill="white")
                image.save(image_path)
                reference = service.register_asset(
                    project.id,
                    role=role,
                    media_type="image",
                    storage_key=image_key,
                    sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
                    byte_size=image_path.stat().st_size,
                )
                service.select_asset(project.id, slot=role, asset_id=reference.id)
            draft = service.create_video_edit_draft(
                project.id,
                dto.VideoEditDraftCreateCommand(
                    sourceVideoAssetId=asset.id,
                    confirmCurrentReferences=True,
                    idempotencyKey=f"seed:{project.id}",
                ),
            )
            edit = repo.get_edit(draft.head_edit_version_id)
            preview = service.create_draft_preview(
                project.id,
                draft.id,
                dto.VideoDraftPreviewCommand(
                    expectedEditVersionId=edit.id,
                    expectedTimelineHash=edit.timeline_hash,
                    idempotencyKey=f"seed-preview:{draft.id}",
                ),
            )
            case = {
                "projectId": str(project.id),
                "draftId": str(draft.id),
                "baseEditVersionId": str(draft.head_edit_version_id),
                "baseVideoAssetId": str(asset.id),
                "previewJobId": str(preview.id),
            }
            cases.append(case)
            return case

        @app.middleware("http")
        async def loopback_control(request: Request, call_next):
            if request.url.path.startswith("/__fixture") and request.client.host not in {
                "127.0.0.1",
                "::1",
            }:
                from fastapi.responses import JSONResponse

                return JSONResponse(status_code=403, content={"detail": "loopback only"})
            return await call_next(request)

        @app.post("/__fixture/control")
        def control(body: dict = Body(...)):
            if "outcome" in body:
                if body["outcome"] not in {
                    "normal",
                    "submission_unknown",
                    "rejection",
                    "result_failure",
                }:
                    raise HTTPException(422, "unknown outcome")
                gateway.next_outcome = body["outcome"]
            if "delaySeconds" in body:
                gateway.delay_seconds = max(0, min(120, float(body["delaySeconds"])))
            if "paused" in body:
                paused.set() if body["paused"] else paused.clear()
            return {"nextOutcome": gateway.next_outcome, "paused": paused.is_set()}

        @app.post("/__fixture/cases")
        def fresh_case(body: dict = Body(default={})):
            return seed_case(body.get("title", "隔离新案例"))

        @app.get("/__fixture/state")
        def diagnostics():
            with sessions() as session:
                jobs = [
                    {
                        "id": str(j.id),
                        "projectId": str(j.project_id),
                        "kind": j.kind,
                        "status": j.status,
                        "provider": j.provider,
                        "taskId": j.provider_task_id,
                        "error": j.error_json,
                        "frozenInput": j.frozen_input_json,
                        "execution": j.execution_json,
                    }
                    for j in session.scalars(select(JobRecord))
                ]
                assets = [
                    {
                        "id": str(a.id),
                        "role": a.role,
                        "jobId": str(a.producing_job_id),
                        "metadata": a.metadata_json,
                    }
                    for a in session.scalars(select(AssetRecord))
                ]
            return {
                "creates": gateway.creates,
                "historicalCreates": historical_creates,
                "queryCount": len(gateway.queries),
                "downloadCount": downloader.downloads,
                "jobs": jobs,
                "assets": assets,
                "cases": cases,
            }

        @app.post("/__fixture/stop")
        def shutdown():
            server.should_exit = True
            return {"stopping": True}

        app.router.routes.append(spa)
        if resumed:
            initial = {
                key: resumed[key]
                for key in (
                    "projectId",
                    "draftId",
                    "baseEditVersionId",
                    "baseVideoAssetId",
                    "previewJobId",
                )
            }
            cases.append(initial)
        else:
            initial = seed_case()

        def work(lane):
            worker = DurableJobWorker(
                sessions,
                gateway,
                worker_id=f"fixture-{os.getpid()}-{lane}",
                result_handler=dispatcher,
                receipt_root=directory / "receipts",
                lane=lane,
                poll_backoff_seconds=0.5,
            )
            while not stop.wait(0.1):
                if not paused.is_set():
                    try:
                        worker.run_once()
                    except Exception:
                        logging.exception("fixture worker iteration failed")

        for lane in ("submit", "query", "local"):
            thread = threading.Thread(target=work, args=(lane,), daemon=True)
            thread.start()
            threads.append(thread)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = service.get_job(uuid.UUID(initial["previewJobId"])).status
            if status == "succeeded":
                break
            if status == "failed":
                raise RuntimeError("seed preview failed; inspect fixture log")
            time.sleep(0.2)
        else:
            raise TimeoutError("seed preview did not complete")
        manifest = {
            "baseURL": base_url,
            "pid": os.getpid(),
            "database": name,
            "directory": str(directory),
            "csrfToken": csrf,
            "resumed": resumed is not None,
            "preserveDatabaseOnStop": resumed is not None,
            **initial,
            "stopURL": base_url + "/__fixture/stop",
            "launchCommand": subprocess.list2cmdline(
                [
                    str(ROOT / ".venv/Scripts/python.exe"),
                    str(Path(__file__).resolve()),
                    *(["--resume-manifest", str(directory / "manifest.json")] if resumed else []),
                    *(
                        ["--use-configured-admin"]
                        if args.use_configured_admin
                        else ["--admin-url", args.admin_url]
                    ),
                    "--port",
                    str(args.port),
                    "--ffmpeg",
                    str(ffmpeg),
                    "--ffprobe",
                    str(ffprobe),
                ]
            ),
            "stopCommand": f"Invoke-RestMethod -Method Post -Uri '{base_url}/__fixture/stop'",
        }
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (ROOT / "output/playwright/browser-edit-fixture-latest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print(json.dumps(manifest), flush=True)
        server = uvicorn.Server(
            uvicorn.Config(
                app, host="127.0.0.1", port=args.port, log_level="info", timeout_graceful_shutdown=3
            )
        )
        server.run()
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=130)
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    main()
