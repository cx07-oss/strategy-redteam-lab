"""Build two deterministic, isolated Foundry source-deployment ZIP bundles."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

FIXED_ZIP_TIME = (2026, 8, 23, 0, 0, 0)
PACKAGE_INIT_BYTES = (
    b'"""Minimal package marker for an isolated Hosted Agent bundle."""\n'
)
FORBIDDEN_ORCHESTRATION_MODULES = frozenset(
    {"__main__", "cli", "historical", "offline"}
)


@dataclass(frozen=True)
class RoleIsolationPolicy:
    """Files that distinguish one Hosted Agent role from the other."""

    prompt: str
    excluded_prompt: str
    excluded_application: str


ROLE_ISOLATION_POLICIES = {
    "attacker-hosted": RoleIsolationPolicy(
        prompt="attacker.md",
        excluded_prompt="defender.md",
        excluded_application="defender-hosted",
    ),
    "defender-hosted": RoleIsolationPolicy(
        prompt="defender.md",
        excluded_prompt="attacker.md",
        excluded_application="attacker-hosted",
    ),
}
APPLICATIONS = tuple(ROLE_ISOLATION_POLICIES)


def _strategy_redteam_imports(path: Path) -> set[str]:
    """Return local top-level modules imported by one Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        names: tuple[str, ...]
        if isinstance(node, ast.Import):
            names = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names = (node.module,)
        else:
            continue
        for name in names:
            if name.startswith("strategy_redteam."):
                modules.add(name.split(".", maxsplit=2)[1])
    return modules


def required_source_modules(root: Path, application: str) -> tuple[str, ...]:
    """Resolve the exact shared-module closure required by one Hosted Agent main."""
    if application not in ROLE_ISOLATION_POLICIES:
        raise RuntimeError(f"unknown Hosted Agent application: {application}")
    package_root = root / "src" / "strategy_redteam"
    pending = list(
        _strategy_redteam_imports(root / "apps" / application / "main.py")
    )
    selected: set[str] = set()
    while pending:
        module = pending.pop()
        if module in selected:
            continue
        source = package_root / f"{module}.py"
        if not source.is_file():
            raise RuntimeError(f"required source module is missing: {module}")
        selected.add(module)
        pending.extend(_strategy_redteam_imports(source) - selected)
    forbidden = selected.intersection(FORBIDDEN_ORCHESTRATION_MODULES)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise RuntimeError(f"cross-agent orchestration entered package graph: {names}")
    return tuple(sorted(selected))


def _copy_required_sources(root: Path, application: str, target: Path) -> None:
    """Copy only import-reachable shared modules plus a minimal package marker."""
    source_root = root / "src" / "strategy_redteam"
    package_target = target / "strategy_redteam"
    package_target.mkdir(parents=True)
    (package_target / "__init__.py").write_bytes(PACKAGE_INIT_BYTES)
    for module in required_source_modules(root, application):
        shutil.copyfile(source_root / f"{module}.py", package_target / f"{module}.py")


def _manifest(source: Path) -> bytes:
    files = {
        path.relative_to(source).as_posix(): {
            "byte_length": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(source.rglob("*"))
        if path.is_file() and path.name != "PACKAGE-MANIFEST.json"
    }
    return (
        json.dumps(
            {"schema_version": "1.0", "files": files},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def build(root: Path) -> tuple[Path, ...]:
    root = root.resolve()
    output_root = (root / "dist" / "hosted").resolve()
    if output_root.parent != (root / "dist").resolve():
        raise RuntimeError("hosted output root escaped the repository dist directory")
    lock_path = root / "requirements-hosted.lock"
    if not lock_path.is_file():
        raise RuntimeError("requirements-hosted.lock is required before packaging")
    output_root.mkdir(parents=True, exist_ok=True)
    built: list[Path] = []
    for application in APPLICATIONS:
        policy = ROLE_ISOLATION_POLICIES[application]
        source_app = root / "apps" / application
        with tempfile.TemporaryDirectory(
            prefix=f".{application}-", dir=output_root
        ) as temp_name:
            staged = Path(temp_name)
            shutil.copyfile(source_app / "main.py", staged / "main.py")
            shutil.copyfile(source_app / ".agentignore", staged / ".agentignore")
            shutil.copyfile(lock_path, staged / "requirements.lock")
            (staged / "requirements.txt").write_text(
                "-r requirements.lock\n", encoding="utf-8", newline="\n"
            )
            _copy_required_sources(root, application, staged / "src")
            (staged / "prompts").mkdir()
            shutil.copyfile(
                root / "prompts" / policy.prompt,
                staged / "prompts" / policy.prompt,
            )
            (staged / "config").mkdir()
            shutil.copyfile(
                root / "config" / "attack-policy-v1.yaml",
                staged / "config" / "attack-policy-v1.yaml",
            )
            if (staged / "prompts" / policy.excluded_prompt).exists():
                raise RuntimeError("cross-role prompt entered isolated package")
            if any(policy.excluded_application in path.parts for path in staged.rglob("*")):
                raise RuntimeError("cross-role Hosted Agent entry point entered package")
            (staged / "PACKAGE-MANIFEST.json").write_bytes(_manifest(staged))

            target = output_root / application
            zip_path = output_root / f"{application}.zip"
            if target.exists():
                shutil.rmtree(target)
            if zip_path.exists():
                zip_path.unlink()
            shutil.copytree(staged, target)
            _write_zip(target, zip_path)
            built.append(zip_path)
    return tuple(built)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    for path in build(root):
        print(f"built={path.relative_to(root).as_posix()}")
        print(f"sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
