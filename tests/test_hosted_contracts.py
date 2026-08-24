"""Gate 10 local protocol and package contracts with deterministic fake models."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml
from pydantic import ValidationError
from scripts.build_hosted_packages import (
    APPLICATIONS,
    FORBIDDEN_ORCHESTRATION_MODULES,
    PACKAGE_INIT_BYTES,
    ROLE_ISOLATION_POLICIES,
    build,
    required_source_modules,
)

from strategy_redteam.data import LocalDatasetStore
from strategy_redteam.hosted import (
    ArtifactStoreError,
    AttackerHostedApplication,
    AttackerHostedRequest,
    AttackerHostedResponse,
    AzureBlobDatasetReference,
    AzureBlobDatasetStore,
    DefenderHostedApplication,
    DefenderHostedRequest,
    DefenderHostedResponse,
    LocalArtifactStore,
    LocalDatasetReference,
    LocalFileDatasetStore,
)
from strategy_redteam.offline import (
    DeterministicOfflineReportClient,
    DeterministicOfflineScenarioClient,
    load_offline_config,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_STORE = ROOT / "tests" / "fixtures" / "offline-cache"
FIXTURE_MANIFEST = FIXTURE_STORE / "manifests" / "correlation-break.json"
CONFIG = ROOT / "config" / "example_60_40.yaml"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load package entry point: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _invoke_host(
    host: Any,
    payload: dict[str, object],
    *,
    inspect_openapi: bool = False,
) -> tuple[httpx.Response, httpx.Response | None]:
    transport = httpx.ASGITransport(app=host)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        readiness = await client.get("/readiness")
        assert readiness.status_code == 200
        openapi = (
            await client.get("/invocations/docs/openapi.json")
            if inspect_openapi
            else None
        )
        invocation = await client.post("/invocations", json=payload)
    return invocation, openapi


def _context(tmp_path: Path):
    local = LocalDatasetStore(FIXTURE_STORE)
    stored = local.validate(FIXTURE_MANIFEST)
    config = load_offline_config(CONFIG)
    experiment = config.bind_dataset(stored)
    reference = LocalDatasetReference(
        dataset_id=stored.manifest.dataset_id,
        data_sha256=stored.manifest.sha256,
        manifest_sha256=stored.manifest_sha256,
        manifest_name=FIXTURE_MANIFEST.name,
    )
    store = LocalFileDatasetStore(FIXTURE_STORE)
    proposer = DeterministicOfflineScenarioClient.from_dataset(
        stored,
        config.attack_policy,
        experiment.max_total_scenarios,
    )
    attacker = AttackerHostedApplication(
        dataset_store=store,
        artifact_store=LocalArtifactStore(tmp_path / "attacker-output"),
        proposer=proposer,
        policy=config.attack_policy,
    )
    defender = DefenderHostedApplication(
        dataset_store=store,
        artifact_store=LocalArtifactStore(tmp_path / "defender-output"),
        report_writer=DeterministicOfflineReportClient(),
    )
    return experiment, reference, attacker, defender


def test_two_built_hosts_invoke_locally_with_fixed_data_and_fake_models(
    tmp_path: Path,
) -> None:
    """Each isolated package exposes readiness and typed Invocations locally."""
    build(ROOT)
    attacker_main = _load_module(
        "gate10_attacker_main",
        ROOT / "dist" / "hosted" / "attacker-hosted" / "main.py",
    )
    defender_main = _load_module(
        "gate10_defender_main",
        ROOT / "dist" / "hosted" / "defender-hosted" / "main.py",
    )
    experiment, reference, attacker, defender = _context(tmp_path)
    attacker_request = AttackerHostedRequest(
        experiment=experiment,
        dataset=reference,
    )

    invocation, openapi = asyncio.run(
        _invoke_host(
            attacker_main.create_host(attacker),
            attacker_request.model_dump(mode="json"),
            inspect_openapi=True,
        )
    )
    assert openapi is not None and openapi.status_code == 200
    assert "/invocations" in openapi.json()["paths"]
    assert invocation.status_code == 200, invocation.text
    attacker_response = AttackerHostedResponse.model_validate_json(invocation.content)
    assert 1 <= len(attacker_response.top_results) <= 3
    assert len(attacker_response.top_scenarios) == len(attacker_response.top_results)
    assert len(attacker_response.artifact_references) == 7
    assert attacker_response.execution.candidate_slots_consumed <= 24

    defender_request = DefenderHostedRequest.from_attacker(
        experiment=experiment,
        response=attacker_response,
    )
    invocation, _ = asyncio.run(
        _invoke_host(
            defender_main.create_host(defender),
            defender_request.model_dump(mode="json"),
        )
    )
    assert invocation.status_code == 200, invocation.text
    defender_response = DefenderHostedResponse.model_validate_json(invocation.content)
    assert len(defender_response.defender_verdicts) == len(
        attacker_response.top_scenarios
    )
    assert all(
        verdict.verdict.value == "reproduced"
        for verdict in defender_response.defender_verdicts
    )
    assert defender_response.failure_report_reference.name == "failure_report.json"
    assert len(defender_response.artifact_references) == 2


def test_invocations_rejects_invalid_contract_without_echoing_input(tmp_path: Path) -> None:
    build(ROOT)
    attacker_main = _load_module(
        "gate10_invalid_attacker_main",
        ROOT / "dist" / "hosted" / "attacker-hosted" / "main.py",
    )
    _, _, attacker, _ = _context(tmp_path)
    response, _ = asyncio.run(
        _invoke_host(
            attacker_main.create_host(attacker),
            {"untrusted": "never echo this secret-shaped input"},
        )
    )
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_request_contract"}}
    assert "never echo" not in response.text


def test_source_bundles_are_isolated_locked_and_exclude_local_state() -> None:
    zip_paths = build(ROOT)
    assert len(zip_paths) == 2
    first_hashes = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in zip_paths)
    rebuilt_paths = build(ROOT)
    assert tuple(
        hashlib.sha256(path.read_bytes()).hexdigest() for path in rebuilt_paths
    ) == first_hashes
    disallowed_parts = {
        ".azure",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "artifacts",
        "dist",
        "runs",
    }
    for application, zip_path in zip(APPLICATIONS, zip_paths, strict=True):
        policy = ROLE_ISOLATION_POLICIES[application]
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            assert "main.py" in names
            assert "requirements.lock" in names
            assert "requirements.txt" in names
            assert "PACKAGE-MANIFEST.json" in names
            assert "src/strategy_redteam/hosted.py" in names
            assert "src/strategy_redteam/hosted_server.py" in names
            assert f"prompts/{policy.prompt}" in names
            assert f"prompts/{policy.excluded_prompt}" not in names
            assert all(policy.excluded_application not in Path(name).parts for name in names)
            assert all(
                f"src/strategy_redteam/{module}.py" not in names
                for module in FORBIDDEN_ORCHESTRATION_MODULES
            )
            expected_sources = {
                "src/strategy_redteam/__init__.py",
                *{
                    f"src/strategy_redteam/{module}.py"
                    for module in required_source_modules(ROOT, application)
                },
            }
            assert {
                name for name in names if name.startswith("src/strategy_redteam/")
            } == expected_sources
            assert archive.read("src/strategy_redteam/__init__.py") == PACKAGE_INIT_BYTES
            assert all(not disallowed_parts.intersection(Path(name).parts) for name in names)
            assert all(
                not any(part.lower().endswith(".egg-info") for part in Path(name).parts)
                for name in names
            )
            assert all(Path(name).name != ".env" for name in names)
            assert all(not Path(name).name.startswith(".env.") for name in names)
            assert all("credential" not in Path(name).name.lower() for name in names)
            assert all("secret" not in Path(name).name.lower() for name in names)
            assert all(
                marker not in archive.read(name)
                for name in names
                for marker in (
                    b"AccountKey=",
                    b"SharedAccessSignature=",
                    b"-----BEGIN PRIVATE KEY-----",
                )
            )
            manifest = json.loads(archive.read("PACKAGE-MANIFEST.json"))
            assert set(manifest["files"]) == names - {"PACKAGE-MANIFEST.json"}
            for name, evidence in manifest["files"].items():
                content = archive.read(name)
                assert evidence == {
                    "byte_length": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
        assert zip_path.name == f"{application}.zip"


def test_isolated_packages_import_and_construct_hosts_in_clean_process() -> None:
    build(ROOT)
    script = """
import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root = Path.cwd()
sys.path.insert(0, str(root / "src"))
spec = importlib.util.spec_from_file_location("isolated_hosted_main", root / "main.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.create_host(object()) is not None
"""
    for application in APPLICATIONS:
        package_root = ROOT / "dist" / "hosted" / application
        result = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=package_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not any("__pycache__" in path.parts for path in package_root.rglob("*"))


def test_source_bundle_excludes_generated_and_sensitive_paths(tmp_path: Path) -> None:
    for application in APPLICATIONS:
        application_root = tmp_path / "apps" / application
        application_root.mkdir(parents=True)
        (application_root / "main.py").write_text(
            "import strategy_redteam.module\n", encoding="utf-8"
        )
        (application_root / ".agentignore").write_text(
            "*.egg-info/\n", encoding="utf-8"
        )
    (tmp_path / "requirements-hosted.lock").write_text(
        "example==1.0\n", encoding="utf-8"
    )
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "attacker.md").write_text("prompt\n", encoding="utf-8")
    (tmp_path / "prompts" / "defender.md").write_text("prompt\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "attack-policy-v1.yaml").write_text(
        "schema_version: '1.0'\n", encoding="utf-8"
    )
    included = tmp_path / "src" / "strategy_redteam" / "module.py"
    included.parent.mkdir(parents=True)
    included.write_text("VALUE = 1\n", encoding="utf-8")
    forbidden = {
        "src/.azure/config.json",
        "src/.env",
        "src/.env.local",
        "src/.git/config",
        "src/.mypy_cache/state",
        "src/.pytest_cache/state",
        "src/.ruff_cache/state",
        "src/__pycache__/module.pyc",
        "src/access-credential.txt",
        "src/artifacts/output.json",
        "src/client-secret.txt",
        "src/debug.log",
        "src/dist/generated.txt",
        "src/orphan.egg-info",
        "src/runs/output.json",
        "src/strategy_redteam.egg-info/PKG-INFO",
    }
    for relative in sorted(forbidden):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("forbidden\n", encoding="utf-8")

    for zip_path in build(tmp_path):
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("PACKAGE-MANIFEST.json"))
        assert "src/strategy_redteam/module.py" in names
        assert "src/strategy_redteam/__init__.py" in names
        assert names.isdisjoint(forbidden)
        assert set(manifest["files"]).isdisjoint(forbidden)


def test_agentignore_excludes_generated_package_metadata() -> None:
    for application in APPLICATIONS:
        policy = ROLE_ISOLATION_POLICIES[application]
        rules = (ROOT / "apps" / application / ".agentignore").read_text(
            encoding="utf-8"
        ).splitlines()
        assert "*.egg-info/" in rules
        assert ".git/" in rules
        assert "dist/" in rules
        assert f"prompts/{policy.excluded_prompt}" in rules
        assert f"apps/{policy.excluded_application}/" in rules
        assert f"{policy.excluded_application}/" in rules
        assert all(
            f"src/strategy_redteam/{module}.py" in rules
            for module in FORBIDDEN_ORCHESTRATION_MODULES
        )


def test_unified_azure_yaml_uses_current_source_and_protocol_contract() -> None:
    payload = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))
    services = payload["services"]
    assert "hooks" not in payload
    assert services["ai-project"] == {
        "host": "azure.ai.project",
        "endpoint": "${AZURE_EXISTING_FOUNDRY_PROJECT_ENDPOINT}",
    }
    for name in APPLICATIONS:
        service = services[name]
        assert service["host"] == "azure.ai.agent"
        assert service["kind"] == "hosted"
        assert service["project"] == f"dist/hosted/{name}"
        assert "hooks" not in service
        assert service["protocols"] == [
            {"protocol": "invocations", "version": "2.0.0"}
        ]
        assert service["codeConfiguration"] == {
            "runtime": "python_3_13",
            "entryPoint": "main.py",
            "dependencyResolution": "remote_build",
        }
        assert "identity" not in service
        assert all(not key.startswith("FOUNDRY_") for key in service["env"])
    locked = (ROOT / "requirements-hosted.lock").read_text(encoding="utf-8")
    assert "azure-ai-agentserver-agentframework" not in locked
    assert "azure-ai-agentserver-langgraph" not in locked


class _FakeDownload:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def readall(self) -> bytes:
        return self.content


class _FakeBlob:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def get_blob_properties(self) -> Any:
        return SimpleNamespace(size=len(self.content))

    def download_blob(self, *, max_concurrency: int = 1) -> _FakeDownload:
        assert max_concurrency == 1
        return _FakeDownload(self.content)

    def upload_blob(self, data: bytes, *, overwrite: bool) -> None:
        raise AssertionError("dataset reads must not upload")


class _FakeContainer:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def get_blob_client(self, blob: str) -> _FakeBlob:
        return _FakeBlob(self.blobs[blob])


def test_azure_blob_dataset_store_uses_injected_token_credential_and_hashes() -> None:
    stored = LocalDatasetStore(FIXTURE_STORE).validate(FIXTURE_MANIFEST)
    manifest_bytes = FIXTURE_MANIFEST.read_bytes()
    assert stored.dataset_path is not None
    dataset_bytes = stored.dataset_path.read_bytes()
    credential = object()
    calls: list[tuple[str, object]] = []

    def factory(url: str, supplied_credential: object) -> _FakeContainer:
        calls.append((url, supplied_credential))
        return _FakeContainer(
            {
                "manifests/correlation-break.json": manifest_bytes,
                f"datasets/{stored.manifest.sha256}.parquet": dataset_bytes,
            }
        )

    reference = AzureBlobDatasetReference(
        dataset_id=stored.manifest.dataset_id,
        data_sha256=stored.manifest.sha256,
        manifest_sha256=stored.manifest_sha256,
        container_url="https://research.blob.core.windows.net/immutable-data",
        manifest_blob="manifests/correlation-break.json",
        dataset_blob=f"datasets/{stored.manifest.sha256}.parquet",
    )
    loaded = AzureBlobDatasetStore(
        credential=credential,
        container_factory=factory,
    ).load(reference)
    assert loaded.manifest == stored.manifest
    assert loaded.dataset_path is None
    assert calls == [(reference.container_url, credential)]


def test_dataset_references_reject_traversal_and_credential_urls() -> None:
    sha = "0" * 64
    with pytest.raises(ValidationError):
        LocalDatasetReference(
            dataset_id="dataset",
            data_sha256=sha,
            manifest_sha256=sha,
            manifest_name="../manifest.json",
        )
    with pytest.raises(ValidationError):
        AzureBlobDatasetReference(
            dataset_id="dataset",
            data_sha256=sha,
            manifest_sha256=sha,
            container_url=(
                "https://research.blob.core.windows.net/data?sig=credential-like-value"
            ),
            manifest_blob="manifest.json",
            dataset_blob="dataset.parquet",
        )


def test_local_artifacts_are_immutable_on_duplicate_publication(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    first = store.publish(
        role="defender",
        experiment_id="immutable-experiment",
        artifacts={"failure_report.json": b"first\n"},
    )
    with pytest.raises(ArtifactStoreError):
        store.publish(
            role="defender",
            experiment_id="immutable-experiment",
            artifacts={"failure_report.json": b"replacement\n"},
        )
    assert first[0].sha256 == hashlib.sha256(b"first\n").hexdigest()
    assert (
        tmp_path / "defender" / "immutable-experiment" / "failure_report.json"
    ).read_bytes() == b"first\n"
