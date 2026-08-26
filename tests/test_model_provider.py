"""Gate 12B provider-selection contracts without provider network access."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

import strategy_redteam.model_provider as provider_module
from strategy_redteam.offline import OfflineExperimentConfig, load_offline_config
from strategy_redteam.services import FakeReportWriter, FakeScenarioProposer

ROOT = Path(__file__).resolve().parents[1]


def test_deterministic_provider_is_the_local_config_default() -> None:
    config = load_offline_config(ROOT / "config" / "example_60_40.yaml")

    assert config.model_provider.provider is provider_module.ModelProviderName.DETERMINISTIC
    proposer = FakeScenarioProposer(())
    writer = FakeReportWriter(())
    assert provider_module.build_scenario_proposer(
        config.model_provider, deterministic=proposer
    ) is proposer
    assert (
        provider_module.build_report_writer(config.model_provider, deterministic=writer)
        is writer
    )


def test_provider_configuration_rejects_unknown_names() -> None:
    with pytest.raises(ValidationError):
        provider_module.ModelProviderConfiguration(provider="unsupported")
    with pytest.raises(provider_module.ModelProviderConfigurationError, match="must be one of"):
        provider_module.provider_configuration_from_environment(
            {"STRATEGY_REDTEAM_MODEL_PROVIDER": "unsupported"},
            default=provider_module.ModelProviderName.DETERMINISTIC,
        )


def test_provider_selection_does_not_require_unrelated_configuration() -> None:
    deterministic = provider_module.provider_configuration_from_environment(
        {}, default=provider_module.ModelProviderName.DETERMINISTIC
    )
    foundry = provider_module.provider_configuration_from_environment(
        {"STRATEGY_REDTEAM_MODEL_PROVIDER": "foundry"},
        default=provider_module.ModelProviderName.DETERMINISTIC,
    )

    assert deterministic.provider is provider_module.ModelProviderName.DETERMINISTIC
    assert foundry.provider is provider_module.ModelProviderName.FOUNDRY


def test_foundry_selection_uses_existing_role_contracts_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposer = FakeScenarioProposer(())
    writer = FakeReportWriter(())
    monkeypatch.setattr(provider_module, "_foundry_scenario_proposer", lambda: proposer)
    monkeypatch.setattr(provider_module, "_foundry_report_writer", lambda: writer)
    configuration = provider_module.ModelProviderConfiguration(provider="foundry")

    assert provider_module.build_scenario_proposer(configuration) is proposer
    assert provider_module.build_report_writer(configuration) is writer


def test_ollama_selection_constructs_its_existing_role_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = provider_module.ModelProviderConfiguration(provider="ollama")
    proposer = FakeScenarioProposer(())
    writer = FakeReportWriter(())
    monkeypatch.setattr(provider_module, "_ollama_scenario_proposer", lambda: proposer)
    monkeypatch.setattr(provider_module, "_ollama_report_writer", lambda: writer)

    assert provider_module.build_scenario_proposer(configuration) is proposer
    assert provider_module.build_report_writer(configuration) is writer


def test_ollama_model_identifier_is_available_for_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRATEGY_REDTEAM_OLLAMA_MODEL", "qwen3:4b")

    assert provider_module.configured_model_identifier(
        provider_module.ModelProviderConfiguration(provider="ollama")
    ) == "qwen3:4b"
    assert (
        provider_module.configured_model_identifier(
            provider_module.ModelProviderConfiguration(provider="deterministic")
        )
        is None
    )


def test_services_continue_to_depend_on_domain_specific_protocols() -> None:
    source = inspect.getsource(__import__("strategy_redteam.services", fromlist=["*"]))

    assert "class ScenarioProposer(Protocol):" in source
    assert "class ReportWriter(Protocol):" in source
    assert "FoundryChatClient" not in source
    assert "Ollama" not in source


def test_offline_config_accepts_explicit_provider_without_changing_default_contract() -> None:
    values = load_offline_config(ROOT / "config" / "example_60_40.yaml").model_dump(
        mode="python"
    )
    values["model_provider"] = {"provider": "ollama"}
    config = OfflineExperimentConfig.model_validate(
        values
    )

    assert config.model_provider.provider is provider_module.ModelProviderName.OLLAMA
