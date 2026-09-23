"""Exercise the actual FFmpeg renderer with synthetic, explicitly local fixtures."""
import hashlib
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from catflow.application.production_selection import UnitSelectionDto, assemble_timeline
from catflow.application.service import JobDto, ProjectCreate, StudioService
from catflow.infrastructure.database import DatabaseSettings, create_database_engine, create_session_factory
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.media_jobs import LocalMediaJobExecutor


@pytest.mark.parametrize("total_frames", [360,1080,1440])
def test_real_edl_v3_render_keeps_long_work_frame_count_and_audio(tmp_path,total_frames):
    engine=create_database_engine(DatabaseSettings.from_env())
    sessions=create_session_factory(engine)
    repo=PostgresStudioRepository(sessions)
    service=StudioService(repo)
    project=service.create_project(ProjectCreate(title="本地帧数验证",theme="合成测试素材",targetDurationSeconds=total_frames//24))
    store=LocalMediaStore(tmp_path/'media')
    executor=LocalMediaJobExecutor(sessions,store,ffmpeg_path=Path(os.environ['FFMPEG_PATH']),ffprobe_path=Path(os.environ['FFPROBE_PATH']))
    now=datetime.now(UTC)
    job=repo.create_job(JobDto(id=uuid.uuid4(),projectId=project.id,kind="render_export",status="queued",
        inputHash="f"*64,idempotencyKey=f"render-test-{project.id}",provider="local_ffmpeg",model="ffmpeg-edl-v3",
        frozenInput={},createdAt=now,updatedAt=now))
    selections=[]
    try:
        for index in range(total_frames//360):
            key=f"fixture/{index}.mp4";path=store.resolve(key);path.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run([os.environ['FFMPEG_PATH'],'-v','error','-y','-f','lavfi','-i',
                f"color=c={['red','green','blue','yellow'][index]}:s=180x320:r=24:d=15",
                '-f','lavfi','-i','sine=frequency=200:sample_rate=48000:duration=15',
                '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(path)],check=True)
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            source_job=repo.create_job(JobDto(id=uuid.uuid4(),projectId=project.id,kind="generate_video",status="succeeded",
                inputHash=sha,idempotencyKey=f"local-fixture-{project.id}-{index}",provider="local_ffmpeg",model="synthetic-test",
                frozenInput={"purpose":"local_test_fixture"},createdAt=now,updatedAt=now))
            # Fixture provenance represents a completed unit without calling Ark.
            from catflow.infrastructure.models import JobRecord
            with sessions.begin() as session:
                session.get(JobRecord,source_job.id).frozen_input_json={"purpose":"production_unit","unitDesignHash":sha}
            asset=service.register_asset(project.id,role="production_unit_video",media_type="video",sha256=sha,
                storage_key=key,producing_job_id=source_job.id,byte_size=path.stat().st_size,
                metadata={"durationFrames":360,"unitDesignHash":sha})
            selections.append(UnitSelectionDto(id=uuid.uuid4(),projectId=project.id,unitId=f"unit-{index}",revision=1,active=True,
                inputHash="f"*64,requestHash="f"*64,idempotencyKey=f"test-{index}",createdAt=now,document={
                    "assetId":str(asset.id),"assetSha256":sha,"audioPolicy":"native" if index%2==0 else "mute",
                    "takes":[{"shotId":f"s{index}-a","sourceInFrame":0,"durationFrames":120},
                             {"shotId":f"s{index}-b","sourceInFrame":120,"durationFrames":240}]}))
        timeline=assemble_timeline(selections)
        result=executor.render_timeline(job.id,timeline.model_dump(mode="json",by_alias=True),tmp_path/'result.mp4')
        assert result['durationFrames']==total_frames
        assert result['frameRateNumerator']==24 and result['frameRateDenominator']==1
        assert result['hasAudio'] is True
        assert result['width']==720 and result['height']==1280
        subprocess.run([os.environ['FFMPEG_PATH'],'-v','error','-i',str(tmp_path/'result.mp4'),'-f','null','-'],check=True)
    finally:
        engine.dispose()
