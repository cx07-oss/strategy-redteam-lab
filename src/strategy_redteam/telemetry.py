"""Portable, provider-neutral telemetry derived from validated run evidence.

This module observes typed workflow outputs.  It neither invokes models nor
calculates financial values, and it deliberately has no access to SDK objects.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field, model_validator

from strategy_redteam.attack import AttackRun, ProposalDecision, ScenarioEvaluationRecord
from strategy_redteam.domain import (
    ContractModel,
    DataManifest,
    DefenderVerdict,
    DefenderVerdictValue,
    Identifier,
    MetricSet,
    SchemaVersion,
)
from strategy_redteam.model_provider import ModelProviderConfiguration, ModelProviderName
from strategy_redteam.services import DefenseRun

TELEMETRY_SCHEMA_VERSION: Final = "1.0"


class TelemetryEventType(StrEnum):
    """The observable transitions of one bounded red-team run."""

    RUN_STARTED = "run_started"
    SCENARIO_ACCEPTED = "scenario_accepted"
    SCENARIO_REJECTED = "scenario_rejected"
    ENGINE_EVALUATION_STARTED = "engine_evaluation_started"
    ENGINE_EVALUATION_COMPLETED = "engine_evaluation_completed"
    RISK_LIMIT_BREACHED = "risk_limit_breached"
    DEFENDER_REPLAY_STARTED = "defender_replay_started"
    DEFENDER_REPLAY_COMPLETED = "defender_replay_completed"
    VERIFICATION_COMPLETED = "verification_completed"
    RUN_COMPLETED = "run_completed"


class TelemetryEvent(ContractModel):
    """One ordered, sanitized event without raw prompts or SDK payloads."""

    sequence: Annotated[int, Field(strict=True, ge=1)]
    event_type: TelemetryEventType
    scenario_id: Identifier | None = None
    round_number: Annotated[int, Field(strict=True, ge=1, le=3)] | None = None
    candidate_number: Annotated[int, Field(strict=True, ge=1, le=8)] | None = None
    rule_id: Identifier | None = None
    verification_verdict: DefenderVerdictValue | None = None


class RunTelemetry(ContractModel):
    """Versioned portable record for a completed attacker/defender workflow."""

    schema_version: SchemaVersion = TELEMETRY_SCHEMA_VERSION
    run_id: Identifier
    experiment_id: Identifier
    strategy_id: Identifier
    dataset_manifest: DataManifest
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_version: Identifier
    seed: Annotated[int, Field(strict=True, ge=0, le=4_294_967_295)]
    provider: ModelProviderName
    model_identifier: str | None = Field(default=None, min_length=1, max_length=256)
    max_rounds: Annotated[int, Field(strict=True, ge=1, le=3)]
    max_candidates_per_round: Annotated[int, Field(strict=True, ge=1, le=8)]
    max_total_scenarios: Annotated[int, Field(strict=True, ge=1, le=24)]
    top_k: Annotated[int, Field(strict=True, ge=1, le=3)]
    baseline_metrics: MetricSet
    evaluations: tuple[ScenarioEvaluationRecord, ...] = Field(max_length=24)
    defender_verdicts: tuple[DefenderVerdict, ...] = Field(max_length=3)
    verification_verdict: DefenderVerdictValue | None = None
    limitations: tuple[str, ...] = Field(min_length=1, max_length=16)
    artifact_references: tuple[str, ...] = Field(default=(), max_length=32)
    events: tuple[TelemetryEvent, ...] = Field(min_length=2, max_length=256)

    @model_validator(mode="after")
    def validate_provenance_and_sequence(self) -> RunTelemetry:
        if self.run_id != self.experiment_id:
            raise ValueError("run_id must equal the immutable experiment identifier")
        if self.evaluations and (
            self.dataset_manifest.sha256 != self.evaluations[0].result.data_sha256
        ):
            raise ValueError("evaluation dataset hash does not match dataset manifest")
        if tuple(event.sequence for event in self.events) != tuple(range(1, len(self.events) + 1)):
            raise ValueError("event sequence must be contiguous and deterministic")
        return self

    def canonical_json_bytes(self) -> bytes:
        """Return validated canonical JSON suitable for portable persistence."""
        return (
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")


def build_run_telemetry(
    *,
    attack_run: AttackRun,
    defense: DefenseRun,
    provider_configuration: ModelProviderConfiguration,
    artifact_references: tuple[str, ...] = (),
) -> RunTelemetry:
    """Copy only validated workflow evidence into a portable typed record."""
    events: list[TelemetryEvent] = [
        TelemetryEvent(sequence=1, event_type=TelemetryEventType.RUN_STARTED)
    ]
    sequence = 1
    for proposal, evaluation in zip(attack_run.proposals, attack_run.evaluations, strict=True):
        sequence += 1
        if proposal.decision is ProposalDecision.REJECTED:
            events.append(
                TelemetryEvent(
                    sequence=sequence,
                    event_type=TelemetryEventType.SCENARIO_REJECTED,
                    scenario_id=proposal.scenario_id,
                    round_number=proposal.round_number,
                    candidate_number=proposal.candidate_number,
                )
            )
            continue
        events.append(
            TelemetryEvent(
                sequence=sequence,
                event_type=TelemetryEventType.SCENARIO_ACCEPTED,
                scenario_id=proposal.scenario_id,
                round_number=proposal.round_number,
                candidate_number=proposal.candidate_number,
            )
        )
        sequence += 1
        events.append(
            TelemetryEvent(
                sequence=sequence,
                event_type=TelemetryEventType.ENGINE_EVALUATION_STARTED,
                scenario_id=proposal.scenario_id,
                round_number=proposal.round_number,
                candidate_number=proposal.candidate_number,
            )
        )
        sequence += 1
        events.append(
            TelemetryEvent(
                sequence=sequence,
                event_type=TelemetryEventType.ENGINE_EVALUATION_COMPLETED,
                scenario_id=proposal.scenario_id,
                round_number=proposal.round_number,
                candidate_number=proposal.candidate_number,
            )
        )
        for breach in evaluation.result.breaches:
            sequence += 1
            events.append(
                TelemetryEvent(
                    sequence=sequence,
                    event_type=TelemetryEventType.RISK_LIMIT_BREACHED,
                    scenario_id=proposal.scenario_id,
                    rule_id=breach.rule_id,
                )
            )
    for replay, verdict in zip(defense.replay_records, defense.verdicts, strict=True):
        sequence += 1
        events.append(
            TelemetryEvent(
                sequence=sequence,
                event_type=TelemetryEventType.DEFENDER_REPLAY_STARTED,
                scenario_id=replay.result.scenario_id,
            )
        )
        sequence += 1
        events.append(
            TelemetryEvent(
                sequence=sequence,
                event_type=TelemetryEventType.DEFENDER_REPLAY_COMPLETED,
                scenario_id=replay.result.scenario_id,
                verification_verdict=verdict.verdict,
            )
        )
    verification = (
        DefenderVerdictValue.REPRODUCED
        if all(v.verdict is DefenderVerdictValue.REPRODUCED for v in defense.verdicts)
        else DefenderVerdictValue.NOT_REPRODUCED
    )
    sequence += 1
    events.append(
        TelemetryEvent(
            sequence=sequence,
            event_type=TelemetryEventType.VERIFICATION_COMPLETED,
            verification_verdict=verification,
        )
    )
    sequence += 1
    events.append(
        TelemetryEvent(
            sequence=sequence,
            event_type=TelemetryEventType.RUN_COMPLETED,
            verification_verdict=verification,
        )
    )
    experiment = attack_run.experiment
    return RunTelemetry(
        run_id=experiment.experiment_id,
        experiment_id=experiment.experiment_id,
        strategy_id=experiment.strategy.strategy_id,
        dataset_manifest=attack_run.dataset_manifest,
        dataset_manifest_sha256=attack_run.dataset_manifest_sha256,
        config_sha256=attack_run.config_sha256,
        code_version=experiment.code_version,
        seed=experiment.seed,
        provider=provider_configuration.provider,
        max_rounds=experiment.max_rounds,
        max_candidates_per_round=experiment.max_candidates_per_round,
        max_total_scenarios=experiment.max_total_scenarios,
        top_k=experiment.top_k,
        baseline_metrics=attack_run.baseline_metrics,
        evaluations=attack_run.evaluations,
        defender_verdicts=defense.verdicts,
        verification_verdict=verification,
        limitations=defense.report.limitations,
        artifact_references=artifact_references,
        events=tuple(events),
    )
