from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import Field

from .contract import ContractModel

RateCardMetric = Literal[
    "inputTokens",
    "outputTokens",
    "completionTokens",
    "totalTokens",
    "generatedImages",
    "generatedVideoSeconds",
]
RateCardUnit = Literal["million_tokens", "image", "video_second"]


class RateCardItem(ContractModel):
    metric: RateCardMetric
    unit: RateCardUnit
    unit_price_micros: int = Field(alias="unitPriceMicros", ge=0)


class UsageCostResult(ContractModel):
    status: Literal["calculated", "unpriced"]
    actual_cost_micros: int | None = Field(alias="actualCostMicros", default=None)
    charged_metrics: dict[str, int] = Field(alias="chargedMetrics", default_factory=dict)


def rate_card_revision_signature(
    *,
    provider: str,
    model: str,
    revision: str,
    source_url: str | None,
    effective_from: datetime,
    rates: tuple[RateCardItem, ...],
) -> tuple[object, ...]:
    """Build the semantic immutable identity of a rate-card revision.

    A timestamp is kept as a datetime so equivalent UTC and local-timezone
    representations compare as the same instant. Metric ordering is irrelevant.
    """

    normalized_rates = tuple(
        sorted((rate.metric, rate.unit, rate.unit_price_micros) for rate in rates)
    )
    return provider, model, revision, source_url, effective_from, normalized_rates


def calculate_usage_cost(
    usage: dict[str, int], rate_items: tuple[RateCardItem, ...]
) -> UsageCostResult:
    """Calculate a cost from the immutable rates captured when a job was created.

    Only explicitly priced metrics are charged. In particular, a provider's
    ``totalTokens`` is descriptive when input/output token rates are used and is
    not charged a second time.
    """

    charged_metrics: dict[str, int] = {}
    cost_micros = Decimal(0)
    for rate in rate_items:
        quantity = usage.get(rate.metric)
        if quantity is None:
            continue
        if quantity < 0:
            raise ValueError(f"usage metric {rate.metric!r} cannot be negative")

        charged_metrics[rate.metric] = quantity
        if rate.unit == "million_tokens":
            cost_micros += Decimal(quantity) * Decimal(rate.unit_price_micros) / Decimal(
                1_000_000
            )
        else:
            cost_micros += Decimal(quantity) * Decimal(rate.unit_price_micros)

    if not charged_metrics:
        return UsageCostResult(status="unpriced", chargedMetrics={})

    return UsageCostResult(
        status="calculated",
        actualCostMicros=int(cost_micros.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        chargedMetrics=charged_metrics,
    )
