"""Fitness workspace path contract for connector-owned runtime data."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .errors import ValidationError


CANONICAL_STATE_PATH = Path("状态") / "state.yml"
LEGACY_STATE_PATH = Path("当前") / "state.yml"


@dataclass(frozen=True)
class WorkspaceLayout:
    root: Path

    @property
    def state_file(self) -> Path:
        canonical = self.root / CANONICAL_STATE_PATH
        return canonical if canonical.is_file() else self.root / LEGACY_STATE_PATH

    @property
    def connector_registry_file(self) -> Path:
        canonical = self.root / "用户" / "数据接入" / "connectors.json"
        if canonical.is_file():
            return canonical
        return self.root / "个人" / "数据源" / "connectors.json"

    @property
    def runtime_dir(self) -> Path:
        return self.root / "运行"

    @property
    def private_dir(self) -> Path:
        return self.runtime_dir / "private"

    @property
    def backups_dir(self) -> Path:
        return self.runtime_dir / "backups" / "xunji-mac"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_dir / "logs" / "fitness-data-bridge"

    @property
    def receipts_dir(self) -> Path:
        return self.runtime_dir / "receipts" / "data-bridge"

    @property
    def apple_health_dir(self) -> Path:
        canonical = self.root / "数据" / "体况" / "apple-health"
        legacy = self.root / "事实" / "体况" / "apple-health"
        return canonical if canonical.exists() or not legacy.exists() else legacy

    @property
    def training_facts_dir(self) -> Path:
        canonical = self.root / "数据" / "训练" / "xunji" / "mac_student_facts"
        legacy = self.root / "事实" / "训练" / "xunji" / "mac_student_facts"
        return canonical if canonical.exists() or not legacy.exists() else legacy

    @property
    def retained_cache_dir(self) -> Path:
        return self.runtime_dir / "cache" / "xunji" / "mac_localtrains"


def resolve_workspace(value: str | Path | WorkspaceLayout | None = None) -> WorkspaceLayout:
    if isinstance(value, WorkspaceLayout):
        return value
    explicit = value or os.environ.get("FITNESS_WORKSPACE")
    candidates = [Path(explicit).expanduser()] if explicit else []
    if not explicit:
        candidates.extend([Path.cwd(), *Path.cwd().parents, Path.home() / "OneDrive" / "Fitness"])
    for candidate in candidates:
        root = candidate.resolve()
        if (root / CANONICAL_STATE_PATH).is_file() or (root / LEGACY_STATE_PATH).is_file():
            return WorkspaceLayout(root)
    raise ValidationError(f"Not a Fitness v5 workspace: {candidates[0].resolve()}")


def resolve_workspace_path(root: Path, value: str, *, field: str) -> Path:
    path = Path(value).expanduser()
    resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValidationError(f"{field} must stay inside the Fitness workspace")
    return resolved


def _layout(value: str | Path | WorkspaceLayout | None = None) -> WorkspaceLayout:
    return resolve_workspace(value)


def default_credentials_file(value: str | Path | WorkspaceLayout | None = None) -> Path:
    return _layout(value).private_dir / "credentials.json"


def default_api_key_file(value: str | Path | WorkspaceLayout | None = None) -> Path:
    return default_credentials_file(value)


def default_api_cache_dir(value: str | Path | WorkspaceLayout | None = None) -> Path:
    return _layout(value).runtime_dir / "cache" / "xunji" / "training-api"


def default_open_api_cache_dir(value: str | Path | WorkspaceLayout | None = None) -> Path:
    return _layout(value).runtime_dir / "cache" / "xunji" / "open-api"


def default_upsert_cache_dir(value: str | Path | WorkspaceLayout | None = None) -> Path:
    return _layout(value).runtime_dir / "cache" / "xunji" / "upsert"
