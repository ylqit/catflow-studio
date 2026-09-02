from __future__ import annotations

from catflow.domain.billing import RateCardItem, calculate_usage_cost


def test_usage_cost_uses_the_frozen_metric_units_without_double_counting_total_tokens() -> None:
    result = calculate_usage_cost(
        {
            "inputTokens": 120_000,
            "outputTokens": 80_000,
            "totalTokens": 200_000,
        },
        (
            RateCardItem(metric="inputTokens", unit="million_tokens", unitPriceMicros=2_000_000),
            RateCardItem(metric="outputTokens", unit="million_tokens", unitPriceMicros=5_000_000),
        ),
    )

    assert result.status == "calculated"
    assert result.actual_cost_micros == 640_000
    assert result.charged_metrics == {"inputTokens": 120_000, "outputTokens": 80_000}


def test_usage_without_a_matching_rate_is_reported_as_unpriced_not_zero() -> None:
    result = calculate_usage_cost(
        {"completionTokens": 9_600, "totalTokens": 9_600},
        (),
    )

    assert result.status == "unpriced"
    assert result.actual_cost_micros is None
    assert result.charged_metrics == {}


def test_image_and_video_second_rates_use_whole_usage_units() -> None:
    result = calculate_usage_cost(
        {"generatedImages": 2, "generatedVideoSeconds": 12},
        (
            RateCardItem(metric="generatedImages", unit="image", unitPriceMicros=250_000),
            RateCardItem(
                metric="generatedVideoSeconds", unit="video_second", unitPriceMicros=100_000
            ),
        ),
    )

    assert result.actual_cost_micros == 1_700_000
