from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete

from catflow.application.service import RateCardRevisionCreateCommand, StudioService
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import ProviderRateCardRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository


def test_postgres_rate_card_revisions_are_immutable_and_only_latest_is_active() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    suffix = uuid.uuid4().hex
    model = f"test-model-{suffix}"
    revisions = (f"test-r1-{suffix}", f"test-r2-{suffix}")
    try:
        first_command = RateCardRevisionCreateCommand(
            provider="ark",
            model=model,
            revision=revisions[0],
            sourceUrl="https://example.test/rates/1",
            effectiveFrom=datetime.now(UTC) - timedelta(days=1),
            rates=[
                {
                    "metric": "inputTokens",
                    "unit": "million_tokens",
                    "unitPriceMicros": 1_000_000,
                },
                {
                    "metric": "outputTokens",
                    "unit": "million_tokens",
                    "unitPriceMicros": 2_000_000,
                },
            ],
        )
        first = service.publish_rate_card(first_command)
        repeated = service.publish_rate_card(first_command)
        second = service.publish_rate_card(
            RateCardRevisionCreateCommand(
                provider="ark",
                model=model,
                revision=revisions[1],
                sourceUrl="https://example.test/rates/2",
                effectiveFrom=datetime.now(UTC),
                rates=[
                    {
                        "metric": "inputTokens",
                        "unit": "million_tokens",
                        "unitPriceMicros": 1_500_000,
                    }
                ],
            )
        )

        by_revision = {
            card.revision: card for card in service.list_rate_cards() if card.model == model
        }
        assert first == repeated
        assert by_revision[first.revision].active is False
        assert by_revision[second.revision].active is True
        assert by_revision[first.revision].rates[0].unit_price_micros == 1_000_000
    finally:
        with sessions.begin() as session:
            session.execute(
                delete(ProviderRateCardRecord).where(ProviderRateCardRecord.model == model)
            )
        engine.dispose()
