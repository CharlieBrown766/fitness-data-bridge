#!/usr/bin/env python3
"""Build a deterministic, privacy-scanned Fitness Data Bridge release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


PLUGIN = "fitness-data-bridge"
VERSION = "1.0.13"
MARKETPLACE_OWNER = "shujian-agent"
COMPONENT_PACKAGE_TYPE = "shujian-component-release"
PUBLIC_DIRECTORIES = (
    ".codex-plugin",
    "assets",
    "fitness_data_bridge",
    "scripts",
    "skills",
)
PUBLIC_FILES = ("AGENTS.md", "requirements.txt")
DISTRIBUTION_FILES = (
    "INSTALL.md",
    "PRIVACY.md",
    "README.md",
    "RELEASE_NOTES.md",
    "SUPPORT.md",
    "TERMS.md",
)
EXCLUDED_COMPONENTS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "backup",
    "backups",
    "cache",
    "caches",
    "logs",
    "private",
    "tests",
}
EXCLUDED_SUFFIXES = {
    ".bak",
    ".db",
    ".log",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".tmp",
    ".zip",
}
PRIVACY_RULES = {
    "private-ipv4": re.compile(
        rb"(?<![0-9])(?:10\.(?:[0-9]{1,3}\.){2}[0-9]{1,3}|"
        rb"192\.168\.(?:[0-9]{1,3}\.)[0-9]{1,3}|"
        rb"172\.(?:1[6-9]|2[0-9]|3[01])\.(?:[0-9]{1,3}\.)[0-9]{1,3})(?![0-9])"
    ),
    "windows-user-profile": re.compile(
        rb"[A-Za-z]:\\Users\\(?![<%$])[^\\/\r\n]+\\", re.IGNORECASE
    ),
    "linux-user-home": re.compile(rb"/home/users/(?![<$])[^/\s]+/", re.IGNORECASE),
    "private-key-header": re.compile(
        rb"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"
    ),
    "authorization-bearer-token": re.compile(
        rb"\bBearer[ \t]+(?!(?:<|\{|\$|%))[A-Za-z0-9][A-Za-z0-9._~-]{19,}\b",
        re.IGNORECASE,
    ),
}


class BuildError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"Invalid JSON file: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BuildError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relative_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"Release tree contains a path alias: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative in files:
                raise BuildError(f"Duplicate release path: {relative}")
            files[relative] = path
    return files


def excluded(relative: str) -> bool:
    parts = relative.replace("\\", "/").split("/")
    leaf = parts[-1].lower()
    return (
        any(part.lower() in EXCLUDED_COMPONENTS for part in parts)
        or Path(leaf).suffix in EXCLUDED_SUFFIXES
        or leaf == ".coverage"
        or leaf == "coverage.xml"
        or leaf.startswith(".env")
        or leaf in {"credentials.json", "secrets.json"}
    )


def copy_public_tree(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise BuildError(f"Missing public directory: {source}")
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"Public source contains a path alias: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        if excluded(relative):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def run_validator(python: str, validator: Path, target: Path, label: str) -> None:
    completed = subprocess.run(
        [python, "-X", "utf8", str(validator), str(target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode:
        detail = (completed.stdout + completed.stderr).strip()
        raise BuildError(f"{label} validation failed:\n{detail}")


def privacy_scan(root: Path) -> int:
    files = relative_files(root)
    for relative, path in files.items():
        lower = path.name.lower()
        if lower.endswith((".key", ".p12", ".pem", ".pfx")):
            raise BuildError(f"Privacy scan failed: sensitive file: {relative}")
        data = path.read_bytes()
        for name, pattern in PRIVACY_RULES.items():
            if pattern.search(data) or pattern.search(data.replace(b"\x00", b"")):
                raise BuildError(f"Privacy scan failed: {name}: {relative}")
    return len(files)


def validate_zip(zip_path: Path, expanded: Path) -> None:
    expanded_files = relative_files(expanded)
    with zipfile.ZipFile(zip_path, "r") as archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        if len(names) != len(set(names)):
            raise BuildError("Zip contains duplicate paths.")
        if set(names) != set(expanded_files):
            raise BuildError("Zip and expanded release contain different file sets.")
        for relative, path in expanded_files.items():
            archived = hashlib.sha256(archive.read(relative)).hexdigest().upper()
            if archived != sha256(path):
                raise BuildError(f"Zip and expanded release differ: {relative}")



def deterministic_zip(source: Path, destination: Path) -> None:
    fixed_time = (2000, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, path in relative_files(source).items():
            info = zipfile.ZipInfo(relative, fixed_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def validator_preflight(python: str, plugin_root: Path) -> tuple[Path, dict]:
    validator = plugin_root / "tools" / "package_validation.py"
    if not validator.is_file():
        raise BuildError(f"Required local package validator not found: {validator}")
    completed = subprocess.run(
        [python, "-X", "utf8", str(validator), "--check-dependencies"],
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode:
        detail = (completed.stdout + completed.stderr).strip()
        raise BuildError(f"Package validator dependency preflight failed:\n{detail}")
    return validator, {
        "id": "agent-package-validation", "version": "1.0.0",
        "sha256": sha256(validator), "scope": "local-package-structure-and-skills",
    }


def _build_staged(args: argparse.Namespace) -> dict:
    plugin_root = Path(__file__).resolve().parents[1]
    package_validator, validator_record = validator_preflight(args.python, plugin_root)
    manifest = load_json(plugin_root / ".codex-plugin" / "plugin.json")
    if manifest.get("name") != PLUGIN or manifest.get("version") != VERSION:
        raise BuildError(f"Plugin identity must be {PLUGIN} {VERSION}.")

    compatibility_template = plugin_root / "templates" / "marketplace" / "compatibility.json"
    compatibility = load_json(compatibility_template)
    if compatibility != {
        "schemaVersion": 1,
        "marketplace": MARKETPLACE_OWNER,
        "plugin": PLUGIN,
        "version": VERSION,
        "requires": ["fitness-planner>=2.0.6"],
    }:
        raise BuildError("Compatibility template does not match the release identity.")

    if args.output_directory:
        output_root = Path(args.output_directory).expanduser().resolve()
    else:
        development_root = plugin_root.parent
        if development_root.name != "development":
            raise BuildError("Default output requires <AgentRoot>/development/fitness-data-bridge.")
        output_root = development_root.parent / "releases" / PLUGIN / VERSION
    try:
        output_root.relative_to(plugin_root)
    except ValueError:
        pass
    else:
        raise BuildError("Release output must be outside the development source.")

    release_name = f"{PLUGIN}-{VERSION}-public"
    release_root = output_root / release_name
    zip_path = output_root / f"{release_name}.zip"
    sidecar_path = Path(f"{zip_path}.sha256")
    for target in (release_root, zip_path, sidecar_path):
        if target.exists():
            raise BuildError(f"Release target already exists: {target}")

    plugin_destination = release_root / "plugins" / PLUGIN
    for directory in PUBLIC_DIRECTORIES:
        copy_public_tree(plugin_root / directory, plugin_destination / directory)
    plugin_destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(plugin_root / ".agents" / "runtime" / "AGENTS.md", plugin_destination / "AGENTS.md")
    shutil.copy2(plugin_root / "requirements.txt", plugin_destination / "requirements.txt")

    marketplace_destination = release_root / ".agents" / "plugins"
    marketplace_destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(compatibility_template, marketplace_destination / "compatibility.json")
    for name in DISTRIBUTION_FILES:
        source = plugin_root / name
        if not source.is_file():
            raise BuildError(f"Missing release document: {source}")
        shutil.copy2(source, release_root / name)

    plugin_validator = skill_validator = package_validator
    run_validator(args.python, plugin_validator, plugin_destination, "Plugin")
    skills = []
    for skill_root in sorted((plugin_destination / "skills").iterdir()):
        if skill_root.is_dir() and (skill_root / "SKILL.md").is_file():
            run_validator(args.python, skill_validator, skill_root, f"Skill {skill_root.name}")
            skills.append(skill_root.name)
    if not skills:
        raise BuildError("Public payload contains no Skills.")

    scanned = privacy_scan(release_root)
    records = [
        {"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)}
        for relative, path in relative_files(release_root).items()
    ]
    write_json(
        release_root / "SHA256SUMS.json",
        {
            "schemaVersion": 1,
            "algorithm": "SHA256",
            "plugin": PLUGIN,
            "version": VERSION,
            "packageType": COMPONENT_PACKAGE_TYPE,
            "marketplaceOwner": MARKETPLACE_OWNER,
            "marketplaceGenerated": False,
            "validation": {
                "validator": validator_record,
                "plugin": "PASS",
                "skills": skills,
                "privacy": "PASS",
                "component_contract": "PASS",
            },
            "privacyRules": sorted(PRIVACY_RULES),
            "scannedFiles": scanned,
            "files": records,
        },
    )
    output_root.mkdir(parents=True, exist_ok=True)
    deterministic_zip(release_root, zip_path)
    validate_zip(zip_path, release_root)
    zip_hash = sha256(zip_path)
    sidecar_path.write_text(f"{zip_hash}  {zip_path.name}\n", encoding="utf-8")
    return {
        "status": "BUILT",
        "validator": validator_record,
        "plugin": PLUGIN,
        "version": VERSION,
        "package_type": COMPONENT_PACKAGE_TYPE,
        "marketplace_owner": MARKETPLACE_OWNER,
        "marketplace_generated": False,
        "release_root": str(release_root),
        "zip_path": str(zip_path),
        "zip_sha256": zip_hash,
        "skills": skills,
        "privacy_scan": "PASS",
    }


def build(args: argparse.Namespace) -> dict:
    """Validate privately, then publish only complete, verified artifacts."""
    plugin_root = Path(__file__).resolve().parents[1]
    validator_preflight(args.python, plugin_root)
    if args.output_directory:
        output_root = Path(args.output_directory).expanduser().resolve()
    else:
        if plugin_root.parent.name != "development":
            raise BuildError("Default output requires <AgentRoot>/development/fitness-data-bridge.")
        output_root = plugin_root.parent.parent / "releases" / PLUGIN / VERSION
    output_root = output_root.resolve()
    if output_root.is_relative_to(plugin_root):
        raise BuildError("Release output must be outside the development source.")
    release_name = f"{PLUGIN}-{VERSION}-public"
    final_release = output_root / release_name
    final_zip = output_root / f"{release_name}.zip"
    final_sidecar = Path(f"{final_zip}.sha256")
    for target in (final_release, final_zip, final_sidecar):
        if target.exists():
            raise BuildError(f"Release target already exists: {target}")
    output_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    with tempfile.TemporaryDirectory(prefix=".agent-build-", dir=output_root) as temporary:
        staged_arguments = argparse.Namespace(**vars(args))
        staged_arguments.output_directory = temporary
        result = _build_staged(staged_arguments)
        try:
            # Exclusive creation claims only new paths; existing user artifacts
            # cannot be replaced, including a concurrent build's outputs.
            final_release.mkdir()
            created.append(final_release)
            shutil.copytree(Path(result["release_root"]), final_release, dirs_exist_ok=True)
            for source, target in ((Path(result["zip_path"]), final_zip), (Path(result["zip_path"] + ".sha256"), final_sidecar)):
                with target.open("xb") as destination:
                    created.append(target)
                    with source.open("rb") as origin:
                        shutil.copyfileobj(origin, destination)
        except BaseException:
            for target in reversed(created):
                resolved = target.resolve()
                if not resolved.is_relative_to(output_root) or resolved == output_root:
                    raise BuildError("Refusing cleanup outside the verified output directory")
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
            raise
    result["release_root"] = str(final_release)
    result["zip_path"] = str(final_zip)
    if "zip_sha256_path" in result:
        result["zip_sha256_path"] = str(final_sidecar)
    if "file_checksum_manifest" in result:
        result["file_checksum_manifest"] = str(final_release / "SHA256SUMS.json")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", default="")
    parser.add_argument("--codex-home", default="", help="Ignored legacy option; validation uses component-local tools/package_validation.py")
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args()


def main() -> int:
    try:
        result = build(parse_args())
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
