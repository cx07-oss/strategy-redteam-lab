"""Gate 12C local Ollama adapters, tested entirely with a fake client."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from tests.test_services import _batch, _context, _gap, _writer_batch

import strategy_redteam.model_provider as provider_module
from strategy_redteam.domain import AttackBatch
from strategy_redteam.ollama_clients import (
    OllamaConfiguration,
    OllamaProviderError,
    OllamaReportWriter,
    OllamaScenarioProposer,
    ollama_configuration_from_environment,
)
from strategy_redteam.services import DefenderNarrativeBatch


@dataclass
class FakeOllamaClient:
    response: object
    calls: list[dict[str, object]]

    def chat(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(content: str) -> dict[str, object]:
    return {"message": {"content": content}}


def test_scenario_adapter_supplies_schema_and_returns_validated_batch(tmp_path) -> None:
    context = _context(tmp_path)
    batch = _batch(context, _gap(context, "ollama-valid", -0.40))
    client = FakeOllamaClient(_response(batch.model_dump_json()), [])
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )

    result = proposer.propose(
        prompt="fixed instructions", evidence_summary=_attack_summary(context)
    )

    assert AttackBatch.model_validate_json(result) == batch
    assert client.calls[0]["format"] == AttackBatch.model_json_schema()
    assert client.calls[0]["options"] == {"temperature": 0.0}


def test_report_adapter_supplies_schema_and_returns_validated_batch(tmp_path) -> None:
    context = _context(tmp_path)
    narrative = _writer_batch("ollama-report")
    client = FakeOllamaClient(_response(narrative.model_dump_json()), [])
    writer = OllamaReportWriter(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )

    result = writer.write(prompt="fixed instructions", evidence_summary=_defender_summary(context))

    assert DefenderNarrativeBatch.model_validate_json(result) == narrative
    assert client.calls[0]["format"] == DefenderNarrativeBatch.model_json_schema()


@pytest.mark.parametrize("content", ["{not-json", '{"unexpected": true}'])
def test_invalid_structured_output_fails_closed(tmp_path, content: str) -> None:
    context = _context(tmp_path)
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(_response(content), []),
    )

    with pytest.raises(OllamaProviderError, match="invalid AttackBatch"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))


def test_out_of_range_scenario_and_transport_failure_fail_closed(tmp_path) -> None:
    context = _context(tmp_path)
    invalid = _batch(context, _gap(context, "out-of-range", -0.40)).model_dump(mode="json")
    invalid["scenarios"][0]["components"][0]["shocks"]["SPY"] = -1.0
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(_response(__import__("json").dumps(invalid)), []),
    )
    with pytest.raises(OllamaProviderError, match="invalid AttackBatch"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))

    failing = OllamaReportWriter(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(TimeoutError("unreachable"), []),
    )
    with pytest.raises(OllamaProviderError, match="request failed"):
        failing.write(prompt="fixed", evidence_summary=_defender_summary(context))


def test_ollama_provider_selection_has_no_fallback(monkeypatch) -> None:
    proposer = object()
    writer = object()
    monkeypatch.setattr(provider_module, "_ollama_scenario_proposer", lambda: proposer)
    monkeypatch.setattr(provider_module, "_ollama_report_writer", lambda: writer)
    configuration = provider_module.ModelProviderConfiguration(provider="ollama")

    assert provider_module.build_scenario_proposer(configuration) is proposer
    assert provider_module.build_report_writer(configuration) is writer
    ollama = ollama_configuration_from_environment({"STRATEGY_REDTEAM_OLLAMA_MODEL": "qwen"})
    assert ollama.model == "qwen"
    with pytest.raises(Exception, match="OLLAMA_MODEL"):
        ollama_configuration_from_environment({})


def _attack_summary(context):
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
        max_candidates=1,
        remaining_scenarios=1,
        seed=context.experiment.seed,
        strategy=context.experiment.strategy,
        transaction_cost_bps=context.experiment.transaction_cost_bps,
        market_summary=summarize_asset_returns(returns, context.experiment.numeric_tolerance),
        failure_rules=context.experiment.failure_rules,
        policy=context.policy,
    )


def _defender_summary(context):
    from strategy_redteam.services import DefenderEvidenceSummary

    return DefenderEvidenceSummary(experiment_id=context.experiment.experiment_id)
