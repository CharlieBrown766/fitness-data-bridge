"""Portable local plugin/skill checks for Agent component builders.

This is a versioned family build contract, not a replacement for Codex runtime
loading or portal submission review.  It never executes plugin hooks or clients.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


VALIDATOR_ID = "agent-package-validation"
VALIDATOR_VERSION = "1.0.0"
DEPENDENCY_REQUIREMENTS = ("PyYAML>=6,<7",)
MANIFEST_PATHS = (".codex-plugin/plugin.json",)
CONTRACT_SCOPE = "codex-compatible-payload-v1.0"
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_SKILL_FIELDS = {"name", "description", "license", "allowed-tools", "metadata"}
_INTERFACE_STRINGS = {
    "displayName", "shortDescription", "longDescription", "developerName",
    "category", "websiteURL", "privacyPolicyURL", "termsOfServiceURL",
    "brandColor", "composerIcon", "logo", "logoDark",
}
_INTERFACE_IMAGES = {"composerIcon", "logo", "logoDark"}


class PackageValidationError(ValueError):
    """The input or build dependency cannot be validated."""


def validator_provenance() -> dict[str, str]:
    return {
        "id": VALIDATOR_ID,
        "version": VALIDATOR_VERSION,
        "scope": CONTRACT_SCOPE,
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest().upper(),
    }


def check_dependencies() -> dict[str, Any]:
    """Fail before staging outputs if the declared YAML parser is unavailable."""
    try:
        yaml = importlib.import_module("yaml")
    except ImportError as exc:
        raise PackageValidationError(
            "Missing build dependency PyYAML>=6,<7. Install with this Python: "
            f'{sys.executable} -m pip install "PyYAML>=6,<7"'
        ) from exc
    version = getattr(yaml, "__version__", "")
    if not isinstance(version, str) or not re.match(r"^6(?:\.|$)", version):
        raise PackageValidationError(
            "Unsupported PyYAML version; this validator requires PyYAML>=6,<7. "
            f'Install with this Python: {sys.executable} -m pip install "PyYAML>=6,<7"'
        )
    return {
        "status": "PASS",
        "validator": validator_provenance(),
        "dependencies": [{"requirement": DEPENDENCY_REQUIREMENTS[0], "version": version}],
    }


def _root(path: str | Path) -> Path:
    root = Path(path).expanduser().absolute()
    if not root.is_dir():
        raise PackageValidationError("Validation input must be an existing directory")
    _assert_no_alias(root)
    return root


def _is_alias(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    # Do not reject OneDrive cloud placeholders merely for being reparse points.
    # Python versions without is_junction still expose Windows junction tags.
    tag = getattr(path.lstat(), "st_reparse_tag", None)
    return tag in {0xA0000003, 0xA000000C}


def _assert_no_alias(path: Path) -> None:
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            if _is_alias(item):
                raise PackageValidationError("Validation paths must not traverse symlinks or junctions")


def _assert_unaliased_tree(root: Path) -> None:
    pending = [root]
    while pending:
        directory = pending.pop()
        for item in directory.iterdir():
            if _is_alias(item):
                relative = item.relative_to(root).as_posix()
                raise PackageValidationError(f"Payload contains a symlink or junction: {relative}")
            if item.is_dir():
                pending.append(item)


def _text(value: Any, field: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise PackageValidationError(f"{field} must be a {'non-empty ' if nonempty else ''}string")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise PackageValidationError(f"{field} must be an array of strings")
    for item in value:
        _text(item, field)
    return value


def _json(path: Path, field: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise PackageValidationError(f"{field} contains a duplicate JSON key: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError(f"{field} must be readable UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PackageValidationError(f"{field} must contain a JSON object")
    return value


def _local_reference(root: Path, value: Any, field: str, *, directory: bool = False) -> Path:
    raw = _text(value, field)
    if not raw.startswith("./") or "\\" in raw or "\x00" in raw or ".." in raw.split("/"):
        raise PackageValidationError(f"{field} must use a contained ./ path without '..' or aliases")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(":" in part for part in relative.parts):
        raise PackageValidationError(f"{field} must be a relative plugin path")
    target = root.joinpath(*relative.parts)
    _assert_no_alias(target)
    if not target.resolve().is_relative_to(root.resolve()):
        raise PackageValidationError(f"{field} escapes the plugin directory")
    if not (target.is_dir() if directory else target.is_file()):
        kind = "directory" if directory else "file"
        raise PackageValidationError(f"{field} references a missing {kind}")
    return target


def _frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        raise PackageValidationError("SKILL.md must begin with YAML frontmatter")
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", content, re.DOTALL)
    if not match:
        raise PackageValidationError("SKILL.md has invalid frontmatter delimiters")
    yaml = importlib.import_module("yaml")

    class UniqueSafeLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            self.flatten_mapping(node)
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                try:
                    duplicate = key in mapping
                except TypeError as exc:
                    raise PackageValidationError("Skill YAML keys must be scalar values") from exc
                if duplicate:
                    raise PackageValidationError("SKILL.md contains a duplicate YAML key")
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    try:
        value = yaml.load(match.group(1), Loader=UniqueSafeLoader)
    except yaml.YAMLError as exc:
        raise PackageValidationError("SKILL.md contains invalid YAML frontmatter") from exc
    if not isinstance(value, dict):
        raise PackageValidationError("Skill frontmatter must be a YAML mapping")
    return value, content[match.end():]


def validate_skill(path: str | Path) -> dict[str, Any]:
    check_dependencies()
    root = _root(path)
    _assert_unaliased_tree(root)
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        raise PackageValidationError("SKILL.md not found")
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PackageValidationError("SKILL.md must be readable UTF-8") from exc
    frontmatter, body = _frontmatter(content)
    if set(frontmatter) - _SKILL_FIELDS:
        raise PackageValidationError("Skill frontmatter contains unsupported properties")
    name = _text(frontmatter.get("name"), "skill.name")
    if len(name) > 64 or not _NAME.fullmatch(name):
        raise PackageValidationError("Skill name must be hyphen-case and at most 64 characters")
    description = _text(frontmatter.get("description"), "skill.description")
    if len(description) > 1024 or "<" in description or ">" in description:
        raise PackageValidationError("Skill description must be at most 1024 characters without angle brackets")
    if description.strip().startswith("[TODO:"):
        raise PackageValidationError("Skill description contains an unfinished TODO placeholder")
    marker = None
    fence_length = 0
    for line in body.splitlines():
        fence = re.match(r"^[ \t]*(?:(?:[-+*]|\d+[.)])[ \t]+)?(`{3,}|~{3,})(.*)$", line)
        if fence:
            token, suffix = fence.groups()
            if marker is None:
                marker, fence_length = token[0], len(token)
            elif token[0] == marker and len(token) >= fence_length and not suffix.strip():
                marker = None
            continue
        if marker is None and re.fullmatch(r"[ ]{0,3}\[TODO:[^\n]*\][ \t]*", line):
            raise PackageValidationError("Skill instructions contain an unfinished TODO placeholder")
    return {"status": "PASS", "kind": "skill", "name": name, "validator": validator_provenance()}


def _interface(root: Path, value: Any) -> None:
    if not isinstance(value, dict):
        raise PackageValidationError("plugin.interface must be an object")
    for name in _INTERFACE_STRINGS & value.keys():
        _text(value[name], f"plugin.interface.{name}")
    for name in _INTERFACE_IMAGES & value.keys():
        _local_reference(root, value[name], f"plugin.interface.{name}")
    if "capabilities" in value:
        _string_list(value["capabilities"], "plugin.interface.capabilities")
    if "defaultPrompt" in value:
        if isinstance(value["defaultPrompt"], str):
            _text(value["defaultPrompt"], "plugin.interface.defaultPrompt")
        else:
            _string_list(value["defaultPrompt"], "plugin.interface.defaultPrompt")
    if "screenshots" in value:
        for item in _string_list(value["screenshots"], "plugin.interface.screenshots"):
            _local_reference(root, item, "plugin.interface.screenshots")


def validate_plugin(
    path: str | Path,
    expected_name: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    check_dependencies()
    root = _root(path)
    _assert_unaliased_tree(root)
    manifest_path = root / MANIFEST_PATHS[0]
    if not manifest_path.is_file():
        if (root / "plugin.json").is_file():
            raise PackageValidationError(
                "Portable root plugin.json requires versioned portable/extension validation; "
                "this contract supports the .codex-plugin/plugin.json compatibility payload"
            )
        raise PackageValidationError("Plugin is missing .codex-plugin/plugin.json")
    manifest = _json(manifest_path, "plugin manifest")
    name = _text(manifest.get("name"), "plugin.name")
    if not _NAME.fullmatch(name):
        raise PackageValidationError("Plugin name must use lowercase letters, digits and single hyphens")
    version = _text(manifest.get("version"), "plugin.version")
    if not _VERSION.fullmatch(version):
        raise PackageValidationError("Plugin version must have major.minor.patch with optional prerelease/build metadata")
    _text(manifest.get("description"), "plugin.description")
    if expected_name is not None and name != expected_name:
        raise PackageValidationError("Plugin name does not match the builder identity")
    if expected_version is not None and version != expected_version:
        raise PackageValidationError("Plugin version does not match the builder identity")
    portable_present = (root / "plugin.json").is_file()
    if portable_present:
        portable = _json(root / "plugin.json", "portable plugin manifest")
        if portable.get("name") != name or portable.get("version") != version:
            raise PackageValidationError("Portable and compatibility manifest identities differ")
    for field in ("homepage", "repository", "license"):
        if field in manifest:
            _text(manifest[field], f"plugin.{field}")
    if "keywords" in manifest:
        _string_list(manifest["keywords"], "plugin.keywords")
    if "author" in manifest:
        author = manifest["author"]
        if not isinstance(author, dict):
            raise PackageValidationError("plugin.author must be an object")
        for field in ("name", "email", "url"):
            if field in author:
                _text(author[field], f"plugin.author.{field}")
    if "interface" in manifest:
        _interface(root, manifest["interface"])
    config_references = []
    for field in ("mcpServers", "apps", "hooks"):
        if field not in manifest:
            continue
        value = manifest[field]
        if isinstance(value, str):
            config = _local_reference(root, value, f"plugin.{field}")
            _json(config, f"plugin.{field} configuration")
            config_references.append(config.relative_to(root).as_posix())
        elif not isinstance(value, dict):
            raise PackageValidationError(f"plugin.{field} must be an inline object or a contained ./ JSON reference")
    skills = []
    if "skills" in manifest:
        entries = manifest["skills"] if isinstance(manifest["skills"], list) else [manifest["skills"]]
        if not entries:
            raise PackageValidationError("plugin.skills must declare a skill directory")
        seen = set()
        for entry in entries:
            directory = _local_reference(root, entry, "plugin.skills", directory=True)
            skill_files = [directory / "SKILL.md"] if (directory / "SKILL.md").is_file() else sorted(directory.rglob("SKILL.md"))
            if not skill_files:
                raise PackageValidationError("Declared plugin.skills directory contains no SKILL.md")
            for skill_file in skill_files:
                relative = skill_file.parent.relative_to(root).as_posix()
                if relative in seen:
                    continue
                seen.add(relative)
                report = validate_skill(skill_file.parent)
                skills.append({"path": relative, "name": report["name"]})
    return {
        "status": "PASS",
        "kind": "plugin",
        "name": name,
        "version": version,
        "manifest": manifest_path.relative_to(root).as_posix(),
        "portable_manifest_present": portable_present,
        "scope": CONTRACT_SCOPE,
        "skills": skills,
        "configuration_references": config_references,
        "validator": validator_provenance(),
    }


def validate_path(path: str | Path) -> dict[str, Any]:
    root = _root(path)
    if any((root / relative).is_file() for relative in MANIFEST_PATHS) or (root / "plugin.json").is_file():
        return validate_plugin(root)
    if (root / "SKILL.md").is_file():
        return validate_skill(root)
    raise PackageValidationError("Input has neither a plugin manifest nor SKILL.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, help="Plugin payload or skill directory")
    parser.add_argument("--check-dependencies", action="store_true", help="Preflight build dependencies before creating outputs")
    args = parser.parse_args(argv)
    if args.check_dependencies and args.path is not None:
        parser.error("--check-dependencies does not accept a validation path")
    if not args.check_dependencies and args.path is None:
        parser.error("Provide a validation path or --check-dependencies")
    try:
        result = check_dependencies() if args.check_dependencies else validate_path(args.path)
    except (OSError, PackageValidationError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
