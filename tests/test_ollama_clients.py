"""Gate 12C local Ollama adapters, tested entirely with a fake client."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError
from tests.test_services import _attack, _batch, _context, _gap, _writer_batch

import strategy_redteam.model_provider as provider_module
from strategy_redteam.domain import AttackBatch
from strategy_redteam.ollama_clients import (
    OllamaConfiguration,
    OllamaProviderError,
    OllamaReportWriter,
    OllamaScenarioProposer,
    _OllamaScenarioPayload,
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
    assert client.calls[0]["format"] == _OllamaScenarioPayload.model_json_schema()
    assert "YYYY-MM-DD" in client.calls[0]["messages"][0]["content"]
    prompt = client.calls[0]["messages"][0]["content"]
    assert "experiment_id and round_number are application-owned" in prompt
    assert client.calls[0]["options"] == {"temperature": 0.0}


def test_model_envelope_cannot_override_authoritative_attack_batch_context(tmp_path) -> None:
    context = _context(tmp_path)
    scenario = _gap(context, "envelope-owned", -0.40)
    payload = _batch(context, scenario).model_dump(mode="json")
    payload["experiment_id"] = "wrong-experiment"
    payload["round_number"] = 2
    client = FakeOllamaClient(_response(__import__("json").dumps(payload)), [])
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )
    returned = AttackBatch.model_validate_json(
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
    )
    assert returned.experiment_id == context.experiment.experiment_id
    assert returned.round_number == 1
    assert returned.scenarios[0] == scenario
    assert len(client.calls) == 1


def test_valid_ollama_batch_reaches_deterministic_evaluation_with_chart_points(tmp_path) -> None:
    context = _context(tmp_path)
    batch = _batch(context, _gap(context, "ollama-engine", -0.40))
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(_response(batch.model_dump_json()), []),
    )

    run = _attack(tmp_path, context, proposer)

    evaluation = run.evaluations[0]
    assert evaluation.scenario is not None
    assert evaluation.result.status.value == "valid"
    assert evaluation.result.metrics is not None
    assert evaluation.chart_points


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

    with pytest.raises(OllamaProviderError, match="ollama_json_or_schema_validation_failure"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))


def test_out_of_range_scenario_and_transport_failure_fail_closed(tmp_path) -> None:
    context = _context(tmp_path)
    invalid = _batch(context, _gap(context, "out-of-range", -0.40)).model_dump(mode="json")
    invalid["scenarios"][0]["components"][0]["shocks"]["SPY"] = -1.0
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(_response(__import__("json").dumps(invalid)), []),
    )
    with pytest.raises(OllamaProviderError, match="ollama_json_or_schema_validation_failure"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))


def test_timestamp_dates_remain_rejected_without_coercion(tmp_path) -> None:
    context = _context(tmp_path)
    payload = _batch(context, _gap(context, "timestamp", -0.40)).model_dump(mode="json")
    payload["scenarios"][0]["evaluation_start"] = "2024-06-15T00:00:00Z"
    payload["scenarios"][0]["evaluation_end"] = "2024-06-15T23:59:59Z"
    with pytest.raises(ValidationError):
        AttackBatch.model_validate(payload)
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(_response(__import__("json").dumps(payload)), []),
    )
    with pytest.raises(OllamaProviderError, match="ollama_json_or_schema_validation_failure"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))

    failing = OllamaReportWriter(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(TimeoutError("unreachable"), []),
    )
    with pytest.raises(OllamaProviderError, match="ollama_transport_failure"):
        failing.write(prompt="fixed", evidence_summary=_defender_summary(context))


def test_one_correction_retry_uses_the_same_schema_and_then_succeeds(tmp_path) -> None:
    context = _context(tmp_path)
    invalid = _batch(context, _gap(context, "retry", -0.40)).model_dump(mode="json")
    invalid["scenarios"][0]["evaluation_start"] = "2024-06-15T00:00:00Z"
    valid = _batch(context, _gap(context, "retry", -0.40)).model_dump_json()

    class TwoResponses:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.responses = [_response(__import__("json").dumps(invalid)), _response(valid)]

        def chat(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return self.responses.pop(0)

    client = TwoResponses()
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )
    assert (
        AttackBatch.model_validate_json(
            proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
        )
        .scenarios[0]
        .scenario_id
        == "retry"
    )
    assert len(client.calls) == 2
    assert (
        client.calls[0]["format"]
        == client.calls[1]["format"]
        == _OllamaScenarioPayload.model_json_schema()
    )
    correction = client.calls[1]["messages"][0]["content"]
    assert "scenarios.0.evaluation_start: required YYYY-MM-DD date" in correction


def test_schema_failure_reports_safe_paths_and_uses_targeted_correction(tmp_path) -> None:
    context = _context(tmp_path)
    invalid = {"scenarios": [{"evaluation_start": "secret-value"}]}
    valid = _batch(context, _gap(context, "schema-corrected", -0.40)).model_dump_json()

    class TwoResponses:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.responses = [_response(__import__("json").dumps(invalid)), _response(valid)]

        def chat(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return self.responses.pop(0)

    client = TwoResponses()
    result = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    ).propose(prompt="fixed", evidence_summary=_attack_summary(context))

    assert AttackBatch.model_validate_json(result).scenarios[0].scenario_id == "schema-corrected"
    correction = client.calls[1]["messages"][0]["content"]
    assert "scenarios.0.evaluation_start: required YYYY-MM-DD date" in correction
    assert "scenarios.0.evaluation_end: required YYYY-MM-DD date" in correction
    assert "secret-value" not in correction
    assert len(client.calls) == 2


def test_second_schema_failure_exposes_at_most_five_safe_diagnostics(tmp_path) -> None:
    context = _context(tmp_path)
    invalid = {"scenarios": [{}], "unexpected": "secret-value"}

    class TwoResponses:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return _response(__import__("json").dumps(invalid))

    client = TwoResponses()
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )
    with pytest.raises(
        OllamaProviderError, match="ollama_json_or_schema_validation_failure"
    ) as error:
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
    details = str(error.value).split(": ", maxsplit=1)[1].split("; ")
    assert len(details) == 5
    assert all("=" in detail for detail in details)
    assert "secret-value" not in str(error.value)
    assert len(client.calls) == 2


def test_second_invalid_response_fails_closed_after_exactly_two_requests(tmp_path) -> None:
    context = _context(tmp_path)

    class TwoInvalidResponses:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return _response('{"unexpected":true}')

    client = TwoInvalidResponses()
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )
    with pytest.raises(OllamaProviderError, match="ollama_json_or_schema_validation_failure"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    ("client", "detail"),
    [
        (FakeOllamaClient(_response("{not-json"), []), "ollama_json_or_schema_validation_failure"),
        (FakeOllamaClient(TimeoutError("secret transport detail"), []), "ollama_transport_failure"),
    ],
)
def test_safe_ollama_failure_categories_reach_rejection_telemetry(
    tmp_path, client: FakeOllamaClient, detail: str
) -> None:
    context = _context(tmp_path)
    run = _attack(
        tmp_path,
        context,
        OllamaScenarioProposer(
            configuration=OllamaConfiguration(model="local-test"), client=client
        ),
    )

    rejection = run.evaluations[0].result.rejection_detail
    assert detail in rejection
    assert "not-json" not in rejection
    assert "secret transport detail" not in rejection


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
