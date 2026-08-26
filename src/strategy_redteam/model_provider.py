"""Provider selection for the existing typed model-role contracts.

This module deliberately does not define a generic model-client protocol.  The
attacker and defender already depend on the narrower ``ScenarioProposer`` and
``ReportWriter`` contracts in :mod:`strategy_redteam.services`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

from strategy_redteam.domain import ContractModel, SchemaVersion
from strategy_redteam.services import ReportWriter, ScenarioProposer


class ModelProviderName(StrEnum):
    """Recognized model providers."""

    DETERMINISTIC = "deterministic"
    FOUNDRY = "foundry"
    OLLAMA = "ollama"


class ModelProviderConfigurationError(RuntimeError):
    """A model-provider choice is invalid, incomplete, or unavailable."""


class ModelProviderConfiguration(ContractModel):
    """Serializable provider choice, separate from provider-specific settings."""

    schema_version: SchemaVersion = "1.0"
    provider: ModelProviderName = ModelProviderName.DETERMINISTIC


def provider_configuration_from_environment(
    environment: Mapping[str, str] | None = None,
    *,
    default: ModelProviderName,
) -> ModelProviderConfiguration:
    """Read one non-secret provider selector without importing a provider SDK."""
    source = os.environ if environment is None else environment
    raw = source.get("STRATEGY_REDTEAM_MODEL_PROVIDER", default.value)
    try:
        return ModelProviderConfiguration(provider=ModelProviderName(raw))
    except ValueError as error:
        raise ModelProviderConfigurationError(
            "STRATEGY_REDTEAM_MODEL_PROVIDER must be one of: deterministic, foundry, ollama"
        ) from error


def _foundry_scenario_proposer() -> ScenarioProposer:
    """Construct Foundry lazily so local execution needs no hosted dependencies."""
    from strategy_redteam.foundry_clients import FoundryScenarioProposer, foundry_configuration

    project_endpoint, model = foundry_configuration()
    return FoundryScenarioProposer(project_endpoint=project_endpoint, model=model)


def _foundry_report_writer() -> ReportWriter:
    """Construct Foundry lazily so local execution needs no hosted dependencies."""
    from strategy_redteam.foundry_clients import FoundryReportWriter, foundry_configuration

    project_endpoint, model = foundry_configuration()
    return FoundryReportWriter(project_endpoint=project_endpoint, model=model)


def _ollama_scenario_proposer() -> ScenarioProposer:
    from strategy_redteam.ollama_clients import (
        OllamaScenarioProposer,
        ollama_configuration_from_environment,
    )

    return OllamaScenarioProposer(configuration=ollama_configuration_from_environment())


def _ollama_report_writer() -> ReportWriter:
    from strategy_redteam.ollama_clients import (
        OllamaReportWriter,
        ollama_configuration_from_environment,
    )

    return OllamaReportWriter(configuration=ollama_configuration_from_environment())


def build_scenario_proposer(
    configuration: ModelProviderConfiguration,
    *,
    deterministic: ScenarioProposer | None = None,
) -> ScenarioProposer:
    """Return the selected existing attacker-role implementation."""
    if configuration.provider is ModelProviderName.DETERMINISTIC:
        if deterministic is None:
            raise ModelProviderConfigurationError(
                "deterministic provider requires a local ScenarioProposer"
            )
        return deterministic
    if configuration.provider is ModelProviderName.FOUNDRY:
        return _foundry_scenario_proposer()
    return _ollama_scenario_proposer()


def build_report_writer(
    configuration: ModelProviderConfiguration,
    *,
    deterministic: ReportWriter | None = None,
) -> ReportWriter:
    """Return the selected existing defender-role implementation."""
    if configuration.provider is ModelProviderName.DETERMINISTIC:
        if deterministic is None:
            raise ModelProviderConfigurationError(
                "deterministic provider requires a local ReportWriter"
            )
        return deterministic
    if configuration.provider is ModelProviderName.FOUNDRY:
        return _foundry_report_writer()
    return _ollama_report_writer()
