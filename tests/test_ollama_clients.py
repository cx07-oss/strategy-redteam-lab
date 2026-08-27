"""Gate 12C local Ollama adapters, tested entirely with a fake client."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest
from pydantic import ValidationError
from tests.test_services import _attack, _batch, _context, _gap, _writer_batch

import strategy_redteam.model_provider as provider_module
from strategy_redteam.domain import AttackBatch
from strategy_redteam.ollama_clients import (
    OllamaConfiguration,
    OllamaFailureCategory,
    OllamaProviderError,
    OllamaReportWriter,
    OllamaScenarioProposer,
    _candidate_slots,
    _stage_one_contract,
    _stage_two_contract,
    _validate_stage_one,
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
        is_default_proposal = self.response == _response(_proposal())
        if len(self.calls) == 2 and is_default_proposal:
            system = kwargs["messages"][0]["content"]  # type: ignore[index]
            slots = sorted(set(re.findall(r'candidate_\d\d', system)))
            return _response(_stage_two(slots))
        if len(self.calls) == 1 and is_default_proposal:
            system = kwargs["messages"][0]["content"]  # type: ignore[index]
            slots = sorted(set(re.findall(r'template_\d\d', system)))
            return _response(
                __import__("json").dumps(
                    {
                        slot: "generic_one_day_gap" for slot in slots
                    }
                )
            )
        return self.response


def _response(content: str) -> dict[str, object]:
    return {"message": {"content": content}}


def _proposal(*, shock: float = -0.40, index: int = 3) -> str:
    """A model response for the no-hypothesis fixture's sole active gap template."""
    return __import__("json").dumps(
        {"template_01": "generic_one_day_gap"}
    )


def _stage_two(slots: list[str] | None = None) -> str:
    slots = ["candidate_01"] if slots is None else slots
    return __import__("json").dumps(
        {slot: {
                "return_row_key": "row_004",
                "spy_one_day_gap": -0.40,
                "hypothesis": "A bounded gap tests the stale allocation.",
                "headline": None,
            } for slot in slots}
    )


def test_scenario_adapter_supplies_schema_and_returns_validated_batch(tmp_path) -> None:
    context = _context(tmp_path)
    client = FakeOllamaClient(_response(_proposal()), [])
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )

    result = proposer.propose(
        prompt="fixed instructions", evidence_summary=_attack_summary(context)
    )

    returned = AttackBatch.model_validate_json(result)
    assert returned.experiment_id == context.experiment.experiment_id
    assert returned.round_number == 1
    assert returned.scenarios[0].components[0].shocks["SPY"] == -0.40
    assert returned.scenarios[0].scenario_id == "ollama-r01-c01"
    assert client.calls[0]["format"] == "json"
    assert client.calls[1]["format"] == "json"
    stage_one_context = client.calls[0]["messages"][1]["content"]
    assert "schema_version" not in stage_one_context
    assert "experiment_id" not in stage_one_context
    assert "round_number" not in stage_one_context
    assert "candidate_slots" not in stage_one_context
    assert "template_01" in stage_one_context
    stage_two_context = client.calls[1]["messages"][1]["content"]
    assert "selected_templates" not in stage_two_context
    assert "Candidate 1 selected template" in stage_two_context
    assert "experiment_id" not in stage_two_context
    assert "YYYY-MM-DD" in client.calls[0]["messages"][0]["content"]
    prompt = client.calls[0]["messages"][0]["content"]
    assert "candidate_index" in prompt
    assert client.calls[0]["options"] == {"temperature": 0.0}
    assert client.calls[0]["think"] is False


def test_json_mode_contracts_do_not_generate_wire_schemas(tmp_path) -> None:
    summary = _attack_summary(_context(tmp_path))
    stage_one = _stage_one_contract(summary)
    stage_two = _stage_two_contract(summary, {"candidate_01": "generic_one_day_gap"})
    assert '"candidate_index"' not in stage_one
    assert '"selections"' not in stage_one
    assert "row_001" in stage_two
    assert "minus_3" in stage_two


def test_stage_two_contract_has_only_selected_candidate_fields(tmp_path) -> None:
    contract = _stage_two_contract(
        _attack_summary(_context(tmp_path)), {"candidate_01": "generic_one_day_gap"}
    )
    assert "spy_one_day_gap" in contract
    assert '"rebalance_offset"' not in contract
    assert "transaction_cost_multiplier" not in contract


def test_fixed_slots_are_application_owned_and_reject_extra_candidates(tmp_path) -> None:
    summary = _attack_summary(_context(tmp_path)).model_copy(
        update={"max_candidates": 3, "remaining_scenarios": 3}
    )
    assert _candidate_slots(summary) == ("candidate_01", "candidate_02", "candidate_03")
    encoded = _stage_one_contract(summary)
    assert all(f"template_{index:02d}" in encoded for index in range(1, 4))
    assert '"candidate_index"' not in encoded
    assert '"selections"' not in encoded
    assert "spy_one_day_gap" not in encoded
    assert "return_row_key" not in encoded
    assert "rebalance_offset" not in encoded

    with pytest.raises(OllamaProviderError, match="ollama_json_or_schema_validation_failure"):
        try:
            _validate_stage_one(
                {
                    f"template_{index:02d}": "generic_one_day_gap"
                    for index, _ in enumerate(_candidate_slots(summary), 1)
                }
                | {"template_04": "generic_one_day_gap"},
                summary,
            )
        except ValidationError as error:
            raise OllamaProviderError(
                OllamaFailureCategory.JSON_OR_SCHEMA
            ) from error


def test_provenance_shaped_stage_one_output_reproduces_run_019_failure(tmp_path) -> None:
    context = _context(tmp_path)
    client = FakeOllamaClient(
        _response('{"schema_version":"1.0","experiment_id":"wrong"}'), []
    )
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )
    with pytest.raises(OllamaProviderError, match="ollama_stage1_validation_failure"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
    assert len(client.calls) == 1


def test_stage_two_uses_semantic_selectors_and_exact_selected_template(tmp_path) -> None:
    summary = _attack_summary(_context(tmp_path))
    encoded = _stage_two_contract(summary, {"candidate_01": "generic_one_day_gap"})
    assert "template_key" not in encoded
    assert '"candidate_index"' not in encoded
    assert "evaluation_start" not in encoded
    assert "row_000" not in encoded
    assert "row_001" in encoded


def test_model_envelope_cannot_override_authoritative_attack_batch_context(tmp_path) -> None:
    context = _context(tmp_path)
    client = FakeOllamaClient(_response(_proposal()), [])
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"), client=client
    )
    returned = AttackBatch.model_validate_json(
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
    )
    assert returned.experiment_id == context.experiment.experiment_id
    assert returned.round_number == 1
    assert returned.scenarios[0].components[0].shocks["SPY"] == -0.40
    assert returned.scenarios[0].scenario_id == "ollama-r01-c01"
    assert len(client.calls) == 2


def test_valid_ollama_batch_reaches_deterministic_evaluation_with_chart_points(tmp_path) -> None:
    context = _context(tmp_path)
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(_response(_proposal()), []),
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

    with pytest.raises(OllamaProviderError, match=r"ollama_(json_parse|stage1_validation)_failure"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))


def test_out_of_range_scenario_and_transport_failure_fail_closed(tmp_path) -> None:
    context = _context(tmp_path)
    invalid = _batch(context, _gap(context, "out-of-range", -0.40)).model_dump(mode="json")
    invalid["scenarios"][0]["components"][0]["shocks"]["SPY"] = -1.0
    proposer = OllamaScenarioProposer(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(_response(__import__("json").dumps(invalid)), []),
    )
    with pytest.raises(OllamaProviderError, match="ollama_stage1_validation_failure"):
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
    with pytest.raises(OllamaProviderError, match="ollama_stage1_validation_failure"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))

    failing = OllamaReportWriter(
        configuration=OllamaConfiguration(model="local-test"),
        client=FakeOllamaClient(TimeoutError("unreachable"), []),
    )
    with pytest.raises(OllamaProviderError, match="ollama_client_failure: TimeoutError"):
        failing.write(prompt="fixed", evidence_summary=_defender_summary(context))


def test_one_correction_retry_uses_the_same_schema_and_then_succeeds(tmp_path) -> None:
    context = _context(tmp_path)
    invalid = {"candidate_01": {}}
    valid = _proposal()

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
    with pytest.raises(OllamaProviderError):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
    assert len(client.calls) == 1


def test_schema_failure_reports_safe_paths_and_uses_targeted_correction(tmp_path) -> None:
    context = _context(tmp_path)
    invalid = {"candidate_01": {}}
    valid = _proposal()

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
    with pytest.raises(OllamaProviderError):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
    assert len(client.calls) == 1


def test_second_schema_failure_exposes_at_most_five_safe_diagnostics(tmp_path) -> None:
    context = _context(tmp_path)
    invalid = {"candidate_01": {}, "unexpected": "secret-value"}

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
        OllamaProviderError, match="ollama_stage1_validation_failure"
    ) as error:
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
    details = str(error.value).split(": ", maxsplit=1)[1].split("; ")
    assert len(details) <= 5
    assert all("=" in detail for detail in details)
    assert "secret-value" not in str(error.value)
    assert len(client.calls) == 1


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
    with pytest.raises(OllamaProviderError, match="ollama_stage1_validation_failure"):
        proposer.propose(prompt="fixed", evidence_summary=_attack_summary(context))
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("client", "detail"),
    [
        (FakeOllamaClient(_response("{not-json"), []), "ollama_json_parse_failure"),
        (FakeOllamaClient(TimeoutError("secret transport detail"), []), "ollama_client_failure"),
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
        return_dates=tuple(timestamp.date() for timestamp in context.dataset.data.index[1:]),
        rebalance_dates=tuple(
            timestamp.date() for timestamp in context.strategy.rebalance_dates(context.dataset)
        ),
    )


def _defender_summary(context):
    from strategy_redteam.services import DefenderEvidenceSummary

    return DefenderEvidenceSummary(experiment_id=context.experiment.experiment_id)
