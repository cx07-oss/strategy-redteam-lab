"""Typed local boundaries for the two Foundry Hosted Agent applications.

The Invocations protocol transports these models as arbitrary JSON. Numeric results
remain owned by the deterministic engine; model text is validated before use.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Self, cast
from urllib.parse import urlsplit

from azure.core.credentials import TokenCredential
from pydantic import Field, StrictBool, StringConstraints, field_validator, model_validator

from strategy_redteam.artifacts import REQUIRED_ARTIFACT_FILES, verify_run_artifacts
from strategy_redteam.attack import (
    AttackPolicy,
    AttackRun,
    ScenarioEvaluationRecord,
    StopReason,
    canonical_json_bytes,
    canonical_json_sha256,
    load_attack_policy,
)
from strategy_redteam.data import (
    DatasetVerificationError,
    LocalDatasetStore,
    StoredDataset,
    validate_dataset_bytes,
)
from strategy_redteam.domain import (
    MAX_ROUNDS,
    MAX_TOTAL_SCENARIOS,
    TOP_K,
    ContractModel,
    DataManifest,
    DefenderVerdict,
    ExperimentSpec,
    FailureReport,
    Identifier,
    MetricSet,
    NonNegativeInt,
    SchemaVersion,
    Sha256,
    StressResult,
)
from strategy_redteam.services import (
    AttackerService,
    DefenderService,
    DefenseRun,
    ReportWriter,
    ScenarioProposer,
)
from strategy_redteam.strategy import StrategyError, strategy_from_spec

MAX_MANIFEST_BYTES = 1_048_576
MAX_DATASET_BYTES = 536_870_912
DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "attack-policy-v1.yaml"

ObjectName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"),
]
ArtifactUri = Annotated[str, StringConstraints(min_length=1, max_length=2048)]


class HostedApplicationError(Exception):
    """A hosted request could not be processed safely."""


class DatasetStoreError(HostedApplicationError):
    """A typed immutable dataset reference could not be verified."""


class ArtifactStoreError(HostedApplicationError):
    """An immutable output artifact could not be published."""


def _validate_object_name(value: str) -> str:
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or "\\" in value:
        raise ValueError("object names must be relative and cannot contain traversal segments")
    return value


def _validate_container_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
        or not parsed.hostname.endswith(".blob.core.windows.net")
        or len([part for part in parsed.path.split("/") if part]) != 1
    ):
        raise ValueError(
            "container_url must be an HTTPS Azure Blob container URL without credentials"
        )
    return value.rstrip("/")


class LocalDatasetReference(ContractModel):
    """Immutable dataset identifiers resolved below a configured local store root."""

    schema_version: SchemaVersion = "1.0"
    kind: Literal["local"] = "local"
    dataset_id: Identifier
    data_sha256: Sha256
    manifest_sha256: Sha256
    manifest_name: ObjectName

    @field_validator("manifest_name")
    @classmethod
    def validate_manifest_name(cls, value: str) -> str:
        return _validate_object_name(value)


class AzureBlobDatasetReference(ContractModel):
    """Content-pinned Azure Blob objects authenticated with Microsoft Entra ID."""

    schema_version: SchemaVersion = "1.0"
    kind: Literal["azure_blob"] = "azure_blob"
    dataset_id: Identifier
    data_sha256: Sha256
    manifest_sha256: Sha256
    container_url: ArtifactUri
    manifest_blob: ObjectName
    dataset_blob: ObjectName

    @field_validator("container_url")
    @classmethod
    def validate_container_url(cls, value: str) -> str:
        return _validate_container_url(value)

    @field_validator("manifest_blob", "dataset_blob")
    @classmethod
    def validate_blob_name(cls, value: str) -> str:
        return _validate_object_name(value)


DatasetReference = Annotated[
    LocalDatasetReference | AzureBlobDatasetReference,
    Field(discriminator="kind"),
]


class DatasetStore(Protocol):
    """Load a content-pinned dataset or fail before numerical evaluation."""

    def load(self, reference: DatasetReference) -> StoredDataset:
        """Return fully verified canonical data for one immutable reference."""


def _verify_reference(reference: DatasetReference, stored: StoredDataset) -> StoredDataset:
    if (
        stored.manifest.dataset_id != reference.dataset_id
        or stored.manifest.sha256 != reference.data_sha256
        or stored.manifest_sha256 != reference.manifest_sha256
    ):
        raise DatasetStoreError("dataset identifiers or immutable hashes did not match")
    return stored


class LocalFileDatasetStore:
    """Resolve typed manifest names below one configured local immutable store."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._local = LocalDatasetStore(self.root)

    def load(self, reference: DatasetReference) -> StoredDataset:
        if not isinstance(reference, LocalDatasetReference):
            raise DatasetStoreError("local store requires a local dataset reference")
        manifests_root = self._local.manifests_dir.resolve()
        manifest_path = (manifests_root / reference.manifest_name).resolve()
        if not manifest_path.is_relative_to(manifests_root):
            raise DatasetStoreError("local manifest resolved outside the configured store")
        try:
            stored = self._local.validate(manifest_path)
        except DatasetVerificationError as error:
            raise DatasetStoreError("local dataset verification failed") from error
        return _verify_reference(reference, stored)


class _BlobDownloader(Protocol):
    def readall(self) -> bytes: ...


class _BlobClient(Protocol):
    def get_blob_properties(self) -> Any: ...

    def download_blob(self, *, max_concurrency: int = 1) -> _BlobDownloader: ...

    def upload_blob(self, data: bytes, *, overwrite: bool) -> Any: ...


class _ContainerClient(Protocol):
    def get_blob_client(self, blob: str) -> _BlobClient: ...


BlobContainerFactory = Callable[[str, TokenCredential], _ContainerClient]


def _default_credential() -> TokenCredential:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def _default_container_factory(
    url: str, credential: TokenCredential
) -> _ContainerClient:
    from azure.storage.blob import ContainerClient

    return cast(
        _ContainerClient,
        ContainerClient.from_container_url(url, credential=credential),
    )


def _blob_bytes(client: _BlobClient, *, maximum: int, label: str) -> bytes:
    try:
        properties = client.get_blob_properties()
        size = int(properties.size)
        if size < 1 or size > maximum:
            raise DatasetStoreError(f"{label} blob size is outside the allowed bound")
        content = client.download_blob(max_concurrency=1).readall()
    except DatasetStoreError:
        raise
    except Exception as error:
        raise DatasetStoreError(f"could not read {label} blob") from error
    if len(content) != size:
        raise DatasetStoreError(f"{label} blob changed during download")
    return content


class AzureBlobDatasetStore:
    """Read immutable Blob references using DefaultAzureCredential or an injected credential."""

    def __init__(
        self,
        *,
        credential: TokenCredential | None = None,
        container_factory: BlobContainerFactory | None = None,
    ) -> None:
        self._credential = credential if credential is not None else _default_credential()
        self._container_factory = container_factory or _default_container_factory

    def load(self, reference: DatasetReference) -> StoredDataset:
        if not isinstance(reference, AzureBlobDatasetReference):
            raise DatasetStoreError("Azure Blob store requires an Azure Blob reference")
        container = self._container_factory(reference.container_url, self._credential)
        manifest_bytes = _blob_bytes(
            container.get_blob_client(reference.manifest_blob),
            maximum=MAX_MANIFEST_BYTES,
            label="manifest",
        )
        if hashlib.sha256(manifest_bytes).hexdigest() != reference.manifest_sha256:
            raise DatasetStoreError("manifest blob SHA-256 did not match the reference")
        dataset_bytes = _blob_bytes(
            container.get_blob_client(reference.dataset_blob),
            maximum=MAX_DATASET_BYTES,
            label="dataset",
        )
        try:
            stored = validate_dataset_bytes(
                manifest_bytes=manifest_bytes,
                parquet_bytes=dataset_bytes,
            )
        except DatasetVerificationError as error:
            raise DatasetStoreError("Azure Blob dataset verification failed") from error
        return _verify_reference(reference, stored)


class DatasetStoreRouter:
    """Dispatch a discriminated dataset reference to its configured store."""

    def __init__(
        self,
        *,
        local: DatasetStore | None = None,
        azure_blob: DatasetStore | None = None,
    ) -> None:
        self.local = local
        self.azure_blob = azure_blob

    def load(self, reference: DatasetReference) -> StoredDataset:
        store = self.local if isinstance(reference, LocalDatasetReference) else self.azure_blob
        if store is None:
            raise DatasetStoreError(f"dataset backend is not configured for {reference.kind}")
        return store.load(reference)


class HostedArtifactReference(ContractModel):
    """Content hash and opaque locator for one immutable published artifact."""

    schema_version: SchemaVersion = "1.0"
    name: ObjectName
    storage: Literal["local", "azure_blob"]
    uri: ArtifactUri
    sha256: Sha256
    byte_length: NonNegativeInt


class ArtifactStore(Protocol):
    """Publish bounded artifacts without overwriting an existing object."""

    def publish(
        self,
        *,
        role: Literal["attacker", "defender"],
        experiment_id: str,
        artifacts: Mapping[str, bytes],
    ) -> tuple[HostedArtifactReference, ...]: ...


def _validate_artifact_name(name: str) -> None:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise ArtifactStoreError("artifact names must be simple file names")


class LocalArtifactStore:
    """Write immutable local artifacts below a configured output root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def publish(
        self,
        *,
        role: Literal["attacker", "defender"],
        experiment_id: str,
        artifacts: Mapping[str, bytes],
    ) -> tuple[HostedArtifactReference, ...]:
        target = (self.root / role / experiment_id).resolve()
        expected_parent = (self.root / role).resolve()
        if target.parent != expected_parent:
            raise ArtifactStoreError("experiment identifier resolved outside artifact root")
        created = False
        try:
            target.mkdir(parents=True, exist_ok=False)
            created = True
            references: list[HostedArtifactReference] = []
            for name in sorted(artifacts):
                _validate_artifact_name(name)
                content = artifacts[name]
                path = target / name
                with path.open("xb") as stream:
                    stream.write(content)
                relative = path.relative_to(self.root).as_posix()
                references.append(
                    HostedArtifactReference(
                        name=name,
                        storage="local",
                        uri=f"local-artifact:///{relative}",
                        sha256=hashlib.sha256(content).hexdigest(),
                        byte_length=len(content),
                    )
                )
        except (OSError, ValueError) as error:
            if created and target.exists():
                shutil.rmtree(target)
            raise ArtifactStoreError("could not publish immutable local artifacts") from error
        return tuple(references)


class AzureBlobArtifactStore:
    """Publish immutable Blob artifacts with Microsoft Entra authentication."""

    def __init__(
        self,
        *,
        container_url: str,
        prefix: str = "strategy-redteam",
        credential: TokenCredential | None = None,
        container_factory: BlobContainerFactory | None = None,
    ) -> None:
        self.container_url = _validate_container_url(container_url)
        self.prefix = _validate_object_name(prefix.strip("/"))
        self._credential = credential if credential is not None else _default_credential()
        self._container_factory = container_factory or _default_container_factory

    def publish(
        self,
        *,
        role: Literal["attacker", "defender"],
        experiment_id: str,
        artifacts: Mapping[str, bytes],
    ) -> tuple[HostedArtifactReference, ...]:
        container = self._container_factory(self.container_url, self._credential)
        safe_experiment_id = _validate_object_name(experiment_id)
        references: list[HostedArtifactReference] = []
        for name in sorted(artifacts):
            _validate_artifact_name(name)
            content = artifacts[name]
            blob_name = f"{self.prefix}/{role}/{safe_experiment_id}/{name}"
            try:
                container.get_blob_client(blob_name).upload_blob(content, overwrite=False)
            except Exception as error:
                raise ArtifactStoreError(
                    "could not publish immutable Azure Blob artifact"
                ) from error
            references.append(
                HostedArtifactReference(
                    name=name,
                    storage="azure_blob",
                    uri=f"{self.container_url}/{blob_name}",
                    sha256=hashlib.sha256(content).hexdigest(),
                    byte_length=len(content),
                )
            )
        return tuple(references)


class AttackExecutionSummary(ContractModel):
    """Bounded trusted fields required for independent defender verification."""

    schema_version: SchemaVersion = "1.0"
    dataset_manifest: DataManifest
    dataset_manifest_sha256: Sha256
    policy: AttackPolicy
    config_sha256: Sha256
    policy_sha256: Sha256
    baseline_metrics: MetricSet
    rounds_started: Annotated[int, Field(strict=True, ge=0, le=MAX_ROUNDS)]
    candidate_slots_consumed: Annotated[
        int, Field(strict=True, ge=0, le=MAX_TOTAL_SCENARIOS)
    ]
    evaluated_scenarios: Annotated[
        int, Field(strict=True, ge=0, le=MAX_TOTAL_SCENARIOS)
    ]
    rejected_scenarios: Annotated[
        int, Field(strict=True, ge=0, le=MAX_TOTAL_SCENARIOS)
    ]
    stop_reason: StopReason
    evidence_condition_met: StrictBool
    attack_completed: StrictBool

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.evaluated_scenarios + self.rejected_scenarios != self.candidate_slots_consumed:
            raise ValueError("attack counts do not consume every candidate slot")
        if canonical_json_sha256(self.policy) != self.policy_sha256:
            raise ValueError("policy hash does not match policy")
        return self

    @classmethod
    def from_run(cls, run: AttackRun) -> Self:
        return cls(
            dataset_manifest=run.dataset_manifest,
            dataset_manifest_sha256=run.dataset_manifest_sha256,
            policy=run.policy,
            config_sha256=run.config_sha256,
            policy_sha256=run.policy_sha256,
            baseline_metrics=run.baseline_metrics,
            rounds_started=run.rounds_started,
            candidate_slots_consumed=run.candidate_slots_consumed,
            evaluated_scenarios=run.evaluated_scenarios,
            rejected_scenarios=run.rejected_scenarios,
            stop_reason=run.stop_reason,
            evidence_condition_met=run.evidence_condition_met,
            attack_completed=run.attack_completed,
        )


class AttackerHostedRequest(ContractModel):
    """Structured attacker invocation payload."""

    schema_version: SchemaVersion = "1.0"
    experiment: ExperimentSpec
    dataset: DatasetReference

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if (
            self.experiment.dataset_id != self.dataset.dataset_id
            or self.experiment.data_sha256 != self.dataset.data_sha256
        ):
            raise ValueError("experiment and dataset reference identifiers must match")
        return self


class AttackerHostedResponse(ContractModel):
    """Bounded attacker evidence returned through POST /invocations."""

    schema_version: SchemaVersion = "1.0"
    experiment_id: Identifier
    dataset: DatasetReference
    top_results: tuple[StressResult, ...] = Field(default=(), max_length=TOP_K)
    top_scenarios: tuple[ScenarioEvaluationRecord, ...] = Field(
        default=(), max_length=TOP_K
    )
    execution: AttackExecutionSummary
    artifact_references: tuple[HostedArtifactReference, ...]

    @model_validator(mode="after")
    def validate_top_evidence(self) -> Self:
        if tuple(record.result for record in self.top_scenarios) != self.top_results:
            raise ValueError("top results must exactly match top scenario evidence")
        if any(result.experiment_id != self.experiment_id for result in self.top_results):
            raise ValueError("top result experiment identifier did not match")
        return self


class DefenderHostedRequest(ContractModel):
    """Independent replay payload constructed from attacker typed evidence."""

    schema_version: SchemaVersion = "1.0"
    experiment: ExperimentSpec
    dataset: DatasetReference
    top_scenarios: tuple[ScenarioEvaluationRecord, ...] = Field(
        default=(), max_length=TOP_K
    )
    execution: AttackExecutionSummary

    @model_validator(mode="after")
    def validate_evidence_identity(self) -> Self:
        if (
            self.experiment.dataset_id != self.dataset.dataset_id
            or self.experiment.data_sha256 != self.dataset.data_sha256
            or self.execution.dataset_manifest.dataset_id != self.dataset.dataset_id
            or self.execution.dataset_manifest.sha256 != self.dataset.data_sha256
            or self.execution.dataset_manifest_sha256 != self.dataset.manifest_sha256
            or self.execution.config_sha256 != canonical_json_sha256(self.experiment)
        ):
            raise ValueError("experiment, dataset, and attacker evidence did not match")
        expected_ranks = tuple(range(1, len(self.top_scenarios) + 1))
        if tuple(record.result.rank for record in self.top_scenarios) != expected_ranks:
            raise ValueError("top scenarios must have contiguous bounded ranks")
        return self

    @classmethod
    def from_attacker(
        cls,
        *,
        experiment: ExperimentSpec,
        response: AttackerHostedResponse,
    ) -> Self:
        return cls(
            experiment=experiment,
            dataset=response.dataset,
            top_scenarios=response.top_scenarios,
            execution=response.execution,
        )


class DefenderHostedResponse(ContractModel):
    """Independent replay verdicts and trusted failure-report reference."""

    schema_version: SchemaVersion = "1.0"
    experiment_id: Identifier
    defender_verdicts: tuple[DefenderVerdict, ...] = Field(
        default=(), max_length=TOP_K
    )
    failure_report: FailureReport
    failure_report_reference: HostedArtifactReference
    artifact_references: tuple[HostedArtifactReference, ...]


def _trace_span(name: str) -> Any:
    try:
        from opentelemetry import trace
    except ImportError:
        return nullcontext()
    return trace.get_tracer("strategy_redteam.hosted").start_as_current_span(name)


def _set_span_attributes(attributes: Mapping[str, str | int | bool]) -> None:
    try:
        from opentelemetry import trace
    except ImportError:
        return
    span = trace.get_current_span()
    for key, value in attributes.items():
        span.set_attribute(key, value)


class AttackerHostedApplication:
    """Load immutable data, run the bounded attack, and publish typed evidence."""

    def __init__(
        self,
        *,
        dataset_store: DatasetStore,
        artifact_store: ArtifactStore,
        proposer: ScenarioProposer,
        policy: AttackPolicy,
    ) -> None:
        self.dataset_store = dataset_store
        self.artifact_store = artifact_store
        self.service = AttackerService(proposer)
        self.policy = policy

    def invoke(self, request: AttackerHostedRequest) -> AttackerHostedResponse:
        with _trace_span("strategy_redteam.attacker"):
            dataset = self.dataset_store.load(request.dataset)
            _set_span_attributes(
                {
                    "strategy_redteam.experiment.id": request.experiment.experiment_id,
                    "strategy_redteam.dataset.id": request.dataset.dataset_id,
                }
            )
            try:
                strategy = strategy_from_spec(
                    request.experiment.strategy,
                    request.experiment.numeric_tolerance,
                )
            except StrategyError as error:
                raise HostedApplicationError("strategy configuration was invalid") from error
            with tempfile.TemporaryDirectory(prefix="strategy-redteam-attacker-") as temp:
                artifact_directory = Path(temp) / "attack"
                run = self.service.run(
                    dataset=dataset,
                    strategy=strategy,
                    experiment=request.experiment,
                    policy=self.policy,
                    artifact_directory=artifact_directory,
                )
                verify_run_artifacts(artifact_directory)
                artifacts = {
                    name: (artifact_directory / name).read_bytes()
                    for name in sorted(REQUIRED_ARTIFACT_FILES)
                }
                references = self.artifact_store.publish(
                    role="attacker",
                    experiment_id=request.experiment.experiment_id,
                    artifacts=artifacts,
                )
            _set_span_attributes(
                {
                    "strategy_redteam.attack.rounds": run.rounds_started,
                    "strategy_redteam.attack.scenarios": run.candidate_slots_consumed,
                    "strategy_redteam.attack.top_count": len(run.top_failures),
                }
            )
            return AttackerHostedResponse(
                experiment_id=request.experiment.experiment_id,
                dataset=request.dataset,
                top_results=tuple(record.result for record in run.top_failures),
                top_scenarios=run.top_failures,
                execution=AttackExecutionSummary.from_run(run),
                artifact_references=references,
            )


class DefenderHostedApplication:
    """Reload data, independently replay top evidence, and publish the report."""

    def __init__(
        self,
        *,
        dataset_store: DatasetStore,
        artifact_store: ArtifactStore,
        report_writer: ReportWriter,
    ) -> None:
        self.dataset_store = dataset_store
        self.artifact_store = artifact_store
        self.service = DefenderService(store=None, report_writer=report_writer)

    @staticmethod
    def _attack_run(request: DefenderHostedRequest) -> AttackRun:
        execution = request.execution
        return AttackRun(
            experiment=request.experiment,
            dataset_manifest=execution.dataset_manifest,
            dataset_manifest_sha256=execution.dataset_manifest_sha256,
            policy=execution.policy,
            config_sha256=execution.config_sha256,
            policy_sha256=execution.policy_sha256,
            baseline_metrics=execution.baseline_metrics,
            proposals=(),
            evaluations=request.top_scenarios,
            top_failures=request.top_scenarios,
            rounds_started=execution.rounds_started,
            candidate_slots_consumed=execution.candidate_slots_consumed,
            evaluated_scenarios=execution.evaluated_scenarios,
            rejected_scenarios=execution.rejected_scenarios,
            stop_reason=execution.stop_reason,
            evidence_condition_met=execution.evidence_condition_met,
            attack_completed=execution.attack_completed,
        )

    def invoke(self, request: DefenderHostedRequest) -> DefenderHostedResponse:
        with _trace_span("strategy_redteam.defender"):
            dataset = self.dataset_store.load(request.dataset)
            _set_span_attributes(
                {
                    "strategy_redteam.experiment.id": request.experiment.experiment_id,
                    "strategy_redteam.dataset.id": request.dataset.dataset_id,
                    "strategy_redteam.defense.input_count": len(request.top_scenarios),
                }
            )
            defense: DefenseRun = self.service.defend(
                attack_run=self._attack_run(request),
                dataset=dataset,
            )
            artifacts = {
                "failure_report.json": canonical_json_bytes(defense.report),
                "failure_report.md": defense.markdown.encode("utf-8"),
            }
            references = self.artifact_store.publish(
                role="defender",
                experiment_id=request.experiment.experiment_id,
                artifacts=artifacts,
            )
            report_reference = next(
                reference
                for reference in references
                if reference.name == "failure_report.json"
            )
            reproduced = sum(
                verdict.verdict.value == "reproduced" for verdict in defense.verdicts
            )
            _set_span_attributes(
                {
                    "strategy_redteam.defense.reproduced": reproduced,
                    "strategy_redteam.defense.verdict_count": len(defense.verdicts),
                }
            )
            return DefenderHostedResponse(
                experiment_id=request.experiment.experiment_id,
                defender_verdicts=defense.verdicts,
                failure_report=defense.report,
                failure_report_reference=report_reference,
                artifact_references=references,
            )


def dataset_store_from_environment() -> DatasetStore:
    """Build a local or Blob DatasetStore without accepting credential secrets."""
    backend = os.environ.get("STRATEGY_REDTEAM_DATASET_BACKEND", "azure_blob")
    if backend == "local":
        root = os.environ.get("STRATEGY_REDTEAM_DATASET_ROOT")
        if root is None:
            raise HostedApplicationError("STRATEGY_REDTEAM_DATASET_ROOT is required")
        return LocalFileDatasetStore(Path(root))
    if backend == "azure_blob":
        return AzureBlobDatasetStore()
    raise HostedApplicationError("unsupported dataset backend")


def artifact_store_from_environment() -> ArtifactStore:
    """Build a local or Blob ArtifactStore without account keys or SAS tokens."""
    backend = os.environ.get("STRATEGY_REDTEAM_ARTIFACT_BACKEND", "azure_blob")
    if backend == "local":
        root = os.environ.get("STRATEGY_REDTEAM_ARTIFACT_ROOT")
        if root is None:
            raise HostedApplicationError("STRATEGY_REDTEAM_ARTIFACT_ROOT is required")
        return LocalArtifactStore(Path(root))
    if backend == "azure_blob":
        container_url = os.environ.get("STRATEGY_REDTEAM_ARTIFACT_CONTAINER_URL")
        if container_url is None:
            raise HostedApplicationError(
                "STRATEGY_REDTEAM_ARTIFACT_CONTAINER_URL is required"
            )
        return AzureBlobArtifactStore(container_url=container_url)
    raise HostedApplicationError("unsupported artifact backend")


def packaged_attack_policy() -> AttackPolicy:
    """Load the versioned policy included in each isolated source bundle."""
    return load_attack_policy(DEFAULT_POLICY_PATH)
