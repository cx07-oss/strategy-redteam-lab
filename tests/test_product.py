"""MVP 3 AI/product boundary tests; all numerical verdicts come from Python."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from strategy_redteam.data import LocalDatasetStore
from strategy_redteam.domain import StressFamily, Symbol
from strategy_redteam.offline import OfflineExperimentConfig
from strategy_redteam.product import (
    AIHypothesis,
    AIProviderMode,
    CanonicalProductArtifact,
    DeterministicHypothesisProvider,
    FailureMetric,
    HypothesisBatch,
    ProposedStressParameters,
    VerificationStatus,
    propose_with_fallback,
    verify_hypotheses,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MANIFEST = ROOT / "tests/fixtures/offline-cache/manifests/correlation-break.json"
REAL_MANIFEST = ROOT / "data/canonical/manifests/spy-tlt-2007-2025.json"
CANONICAL_PRODUCT = ROOT / "data/canonical/canonical-product.json"


def _fixture() -> tuple[object, OfflineExperimentConfig]:
    stored = LocalDatasetStore(FIXTURE_MANIFEST.parent.parent).validate(FIXTURE_MANIFEST)
    config = OfflineExperimentConfig.model_validate(
        yaml.safe_load((ROOT / "config/example_60_40.yaml").read_text())
    )
    return stored, config


def test_real_cached_dataset_provenance_and_order_without_network() -> None:
    stored = LocalDatasetStore(REAL_MANIFEST.parent.parent).validate(REAL_MANIFEST)
    assert stored.manifest.provider == "yfinance"
    assert stored.manifest.row_count == 4780
    assert stored.manifest.start_date == date(2007, 1, 3)
    assert stored.manifest.end_date == date(2025, 12, 31)
    assert stored.manifest.sha256 == (
        "2c3d3b7bd8aede53ffd768e64db71532a48543c0e897e2aba1b4e8f67734426b"
    )
    assert stored.manifest.missing_data_policy == "reject"
    assert stored.data.index.is_monotonic_increasing
    assert not stored.data.isna().any().any()


def test_canonical_product_artifact_validates_against_strict_schema() -> None:
    artifact = CanonicalProductArtifact.model_validate_json(CANONICAL_PRODUCT.read_bytes())
    assert artifact.research.data_manifest.sha256 == (
        "2c3d3b7bd8aede53ffd768e64db71532a48543c0e897e2aba1b4e8f67734426b"
    )
    assert len(artifact.ai_findings) == 3


def test_deterministic_provider_schema_uniqueness_and_engine_verdicts() -> None:
    stored, config = _fixture()
    provider = DeterministicHypothesisProvider()
    batch = provider.propose(stored.data.index[1].date(), stored.data.index[-1].date())
    assert len(batch.hypotheses) == len({item.hypothesis_id for item in batch.hypotheses}) == 3
    findings = verify_hypotheses(
        stored, config.bind_dataset(stored), batch.hypotheses, transaction_cost_bps=10.0
    )
    assert [item.verification_status for item in findings] == [
        VerificationStatus.REJECTED,
        VerificationStatus.REPRODUCED,
        VerificationStatus.REPRODUCED,
    ]
    assert all(item.baseline_metrics == findings[0].baseline_metrics for item in findings)
    assert all(item.evidence.startswith("Deterministic") for item in findings)


def test_malformed_provider_falls_back_and_duplicates_are_rejected() -> None:
    result = propose_with_fallback(
        AIProviderMode.LOCAL,
        "ollama:test",
        lambda: "not-json",
        date(2024, 1, 3),
        date(2024, 4, 23),
    )
    assert result.fallback_used and result.provider_mode is AIProviderMode.DETERMINISTIC
    with pytest.raises(ValidationError, match="hypothesis_id values must be unique"):
        HypothesisBatch(hypotheses=(result.batch.hypotheses[0], result.batch.hypotheses[0]))


def test_unsupported_parameters_fail_closed_or_receive_unsupported_verdict() -> None:
    with pytest.raises(ValidationError, match="require volatility_multiplier"):
        AIHypothesis(
            hypothesis_id="bad",
            title="Bad",
            rationale="Missing supported parameter.",
            targeted_vulnerability="none",
            supported_stress_family=StressFamily.VOLATILITY_MULTIPLIER,
            proposed_parameters=ProposedStressParameters(
                start_date=date(2024, 1, 3),
                end_date=date(2024, 4, 23),
                symbols=(Symbol.SPY, Symbol.TLT),
            ),
            expected_failure_mechanism="None.",
            failure_metric=FailureMetric.ANNUALIZED_VOLATILITY,
            minimum_degradation=0.01,
        )

    stored, config = _fixture()
    original = DeterministicHypothesisProvider().propose(
        date(2020, 1, 1), date(2020, 12, 31)
    ).hypotheses[0]
    finding = verify_hypotheses(
        stored, config.bind_dataset(stored), (original,), transaction_cost_bps=10.0
    )[0]
    assert finding.verification_status is VerificationStatus.UNSUPPORTED
    assert finding.stressed_metrics is None
    assert finding.rejection_reason
