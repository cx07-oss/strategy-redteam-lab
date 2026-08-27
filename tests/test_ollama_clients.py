"""Network-free Phase 3A catalog-selection tests for the Ollama adapter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pytest
from tests.test_services import _attack, _context, _gap

from strategy_redteam.attack import build_attack_catalog
from strategy_redteam.domain import AttackBatch
from strategy_redteam.ollama_clients import (
    OllamaConfiguration,
    OllamaProviderError,
    OllamaScenarioProposer,
    _catalog_choice_slots,
    _catalog_plaintext,
    _validate_catalog_selection,
)


@dataclass
class FakeOllamaClient:
    content: str | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def chat(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.content is not None:
            return {"message": {"content": self.content}}
        catalog = kwargs["messages"][1]["content"]  # type: ignore[index]
        keys = re.findall(r"(?m)^(atk_\d{3}):", catalog)
        slots = tuple(dict.fromkeys(re.findall(r"choice_\d\d", catalog)))
        return {
            "message": {"content": json.dumps(dict(zip(slots, keys[: len(slots)], strict=True)))}
        }


def _catalog(context, count: int = 2):
    return build_attack_catalog(
        tuple(
            _gap(context, f"catalog-source-{index}", -0.20 - index / 100) for index in range(count)
        )
    )


def _summary(context, *, max_candidates: int = 1):
    from strategy_redteam.services import AttackerEvidenceSummary
    from strategy_redteam.strategy import close_prices
    from strategy_redteam.stress import summarize_asset_returns

    returns = close_prices(context.dataset).pct_change(fill_method=None).fillna(0.0)
    returns.columns.name = "symbol"
    return AttackerEvidenceSummary(
        experiment_id=context.experiment.experiment_id,
        dataset_id=context.experiment.dataset_id,
        data_sha256=context.experiment.data_sha256,
        round_number=1,
        max_candidates=max_candidates,
        remaining_scenarios=max_candidates,
        seed=context.experiment.seed,
        strategy=context.experiment.strategy,
        transaction_cost_bps=context.experiment.transaction_cost_bps,
        market_summary=summarize_asset_returns(returns, context.experiment.numeric_tolerance),
        failure_rules=context.experiment.failure_rules,
        policy=context.policy,
        return_dates=tuple(timestamp.date() for timestamp in context.dataset.data.index[1:]),
        rebalance_dates=tuple(
            timestamp.date() for timestamp in context.strategy.rebalance_dates(context.dataset)
        ),
    )


@pytest.mark.parametrize("catalog", [None, build_attack_catalog(())])
def test_missing_or_empty_catalog_fails_closed_without_model_call(tmp_path, catalog) -> None:
    context, client = _context(tmp_path), FakeOllamaClient()
    with pytest.raises(OllamaProviderError, match="ollama_context_validation_failure"):
        OllamaScenarioProposer(
            configuration=OllamaConfiguration(model="local-test"), client=client
        ).propose(prompt="ignored", evidence_summary=_summary(context), attack_catalog=catalog)
    assert client.calls == []


def test_valid_catalog_uses_one_json_nonthinking_call_and_preserves_values(tmp_path) -> None:
    context, client = _context(tmp_path), FakeOllamaClient()
    catalog = _catalog(context)
    result = AttackBatch.model_validate_json(
        OllamaScenarioProposer(
            configuration=OllamaConfiguration(model="local-test"), client=client
        ).propose(prompt="ignored", evidence_summary=_summary(context), attack_catalog=catalog)
    )
    assert len(client.calls) == 1
    assert client.calls[0]["format"] == "json"
    assert client.calls[0]["think"] is False
    supplied = client.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert "CATALOG:" in supplied and "duration_rows" not in supplied and "choice_01" in supplied
    assert result.scenarios[0].scenario_id == "ollama-r01-c01"
    assert result.scenarios[0].model_dump(exclude={"scenario_id"}) == catalog.entries[
        0
    ].scenario.model_dump(exclude={"scenario_id"})


@pytest.mark.parametrize(
    "payload",
    [
        {"choice_01": "atk_999"},
        {},
        {"choice_01": "atk_001", "extra": "atk_002"},
        {"choice_01": "atk_001", "duration_rows": 3},
    ],
)
def test_unknown_missing_extra_and_numeric_output_fail_closed(tmp_path, payload) -> None:
    context, catalog = _context(tmp_path), None
    catalog = _catalog(context)
    client = FakeOllamaClient(json.dumps(payload))
    with pytest.raises(OllamaProviderError, match="ollama_selection_validation_failure"):
        OllamaScenarioProposer(
            configuration=OllamaConfiguration(model="local-test"), client=client
        ).propose(prompt="ignored", evidence_summary=_summary(context), attack_catalog=catalog)
    assert len(client.calls) == 1


def test_duplicate_keys_fail_closed_and_order_is_exact(tmp_path) -> None:
    context, catalog = _context(tmp_path), None
    catalog = _catalog(context)
    summary = _summary(context, max_candidates=2)
    assert _catalog_choice_slots(2) == ("choice_01", "choice_02")
    assert _validate_catalog_selection(
        {"choice_01": "atk_002", "choice_02": "atk_001"}, catalog, 2
    ) == ("atk_002", "atk_001")
    client = FakeOllamaClient(json.dumps({"choice_01": "atk_001", "choice_02": "atk_001"}))
    with pytest.raises(OllamaProviderError, match="ollama_selection_validation_failure"):
        OllamaScenarioProposer(
            configuration=OllamaConfiguration(model="local-test"), client=client
        ).propose(prompt="ignored", evidence_summary=summary, attack_catalog=catalog)
    assert len(client.calls) == 1
    assert "scenario_id" not in _catalog_plaintext(catalog, 2)


def test_real_service_catalog_path_reaches_deterministic_evaluation(tmp_path) -> None:
    context, client = _context(tmp_path), FakeOllamaClient()
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )
    run = _attack(tmp_path, context, proposer)  # type: ignore[arg-type]
    evaluation = run.evaluations[0]
    assert evaluation.scenario is not None
    assert evaluation.result.status.value == "valid"
    assert evaluation.result.metrics is not None
    assert evaluation.chart_points
    assert len(client.calls) == 1
