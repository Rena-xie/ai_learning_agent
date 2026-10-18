"""用量成本估算单元测试。"""

from __future__ import annotations

from learning_agent.domain.entities import UsageRecord, estimate_cost


def test_cost_computed_from_prices() -> None:
    usage = UsageRecord(input_tokens=1000, output_tokens=500)
    priced = estimate_cost(usage, input_price_per_1k=0.001, output_price_per_1k=0.002)
    assert priced.estimated_cost == 0.002
    assert priced.input_tokens == 1000
    assert priced.output_tokens == 500


def test_cost_is_none_without_prices() -> None:
    usage = UsageRecord(input_tokens=1000, output_tokens=500)
    priced = estimate_cost(usage, input_price_per_1k=0.0, output_price_per_1k=0.0)
    assert priced.estimated_cost is None


def test_cost_is_none_when_tokens_unknown() -> None:
    usage = UsageRecord(input_tokens=None, output_tokens=500)
    priced = estimate_cost(usage, input_price_per_1k=0.001, output_price_per_1k=0.002)
    assert priced.estimated_cost is None
