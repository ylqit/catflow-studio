from __future__ import annotations

import hashlib
import json
import os

from pydantic import BaseModel, field_validator
from sqlalchemy import URL, Engine, create_engine, make_url, select, update
from sqlalchemy.orm import Session, sessionmaker

from .models import CanonProfileRecord


class DatabaseSettings(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = make_url(value)
        if parsed.drivername != "postgresql+psycopg":
            raise ValueError("CatFlow requires PostgreSQL with the psycopg driver")
        return value

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        configured_url = os.environ.get("CATFLOW_DATABASE_URL")
        if configured_url:
            return cls(url=configured_url)

        database_url = URL.create(
            drivername="postgresql+psycopg",
            username=os.environ.get("CATFLOW_DB_USER", "postgres"),
            password=os.environ.get("CATFLOW_DB_PASSWORD") or None,
            host=os.environ.get("CATFLOW_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("CATFLOW_DB_PORT", "5432")),
            database=os.environ.get("CATFLOW_DB_NAME", "catflow_studio"),
            query={"sslmode": os.environ.get("CATFLOW_DB_SSLMODE", "prefer")},
        )
        return cls(url=database_url.render_as_string(hide_password=False))


def create_database_engine(settings: DatabaseSettings) -> Engine:
    return create_engine(settings.url, pool_pre_ping=True, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def canon_v4_document() -> dict[str, object]:
    return {
        "profileId": "canon-v4-healing-child-cat-style-board",
        "child": {
            "age": "8-9",
            "identity": "固定同一位8至9岁儿童",
            "hair": "齐下颌短发",
            "lockedTraits": ["脸型", "年龄感", "发型", "身体比例"],
        },
        "cat": {
            "identity": "固定同一只灰白虎斑猫",
            "lockedTraits": ["灰白毛色分区", "虎斑", "眼睛", "鼻口", "环纹尾巴", "四足结构"],
        },
        "style": {
            "positive": [
                "原创二维柔和数字插画",
                "暖灰色细轮廓线",
                "哑光肤色、毛发和布料材质",
                "轻微纸感颗粒",
                "柔和漫射暖光",
            ],
            "negative": [
                "摄影写实",
                "3D塑料玩具质感",
                "角色身份漂移",
                "文字、Logo、水印",
                "叶片、露珠和绿色微距摄影构图",
            ],
        },
        "references": {
            "styleSource": {"providerEligible": False, "role": "style_source"},
            "styleBoard": {"providerEligible": True, "role": "style_board"},
        },
    }


def canon_v4_hash() -> str:
    return hashlib.sha256(
        json.dumps(
            canon_v4_document(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def ensure_canon_v4(session: Session) -> CanonProfileRecord:
    existing = session.scalar(
        select(CanonProfileRecord).where(
            CanonProfileRecord.profile_key == "canon-v4-healing-child-cat-style-board",
            CanonProfileRecord.version == 4,
        )
    )
    if existing is not None:
        if not existing.active:
            session.execute(update(CanonProfileRecord).values(active=False))
            existing.active = True
        return existing
    session.execute(update(CanonProfileRecord).values(active=False))
    profile = CanonProfileRecord(
        profile_key="canon-v4-healing-child-cat-style-board",
        version=4,
        active=True,
        profile_json=canon_v4_document(),
        profile_hash=canon_v4_hash(),
    )
    session.add(profile)
    session.flush()
    return profile
