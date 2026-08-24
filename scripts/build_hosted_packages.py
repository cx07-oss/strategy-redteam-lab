"""Build two deterministic, isolated Foundry source-deployment ZIP bundles."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

APPLICATIONS = ("attacker-hosted", "defender-hosted")
EXCLUDED_DIRECTORY_NAMES = {
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
EXCLUDED_FILE_SUFFIXES = (".log", ".pyc", ".pyo")
FIXED_ZIP_TIME = (2026, 8, 23, 0, 0, 0)


def _copy_tree(source: Path, target: Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(
            part in EXCLUDED_DIRECTORY_NAMES or part.lower().endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        if path.is_dir():
            continue
        lower_name = path.name.lower()
        if (
            lower_name == ".env"
            or lower_name.startswith(".env.")
            or lower_name.endswith(EXCLUDED_FILE_SUFFIXES)
            or "credential" in lower_name
            or "secret" in lower_name
        ):
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


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
            _copy_tree(root / "src", staged / "src")
            _copy_tree(root / "prompts", staged / "prompts")
            (staged / "config").mkdir()
            shutil.copyfile(
                root / "config" / "attack-policy-v1.yaml",
                staged / "config" / "attack-policy-v1.yaml",
            )
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
