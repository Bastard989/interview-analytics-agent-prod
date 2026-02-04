"""
Release policy checks for CI/CD.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - only on Python < 3.11
    tomllib = None  # type: ignore[assignment]

_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")


def extract_release_version_from_tag(tag: str) -> str:
    raw = (tag or "").strip()
    m = _TAG_RE.match(raw)
    if not m:
        raise ValueError("release tag must match v<major>.<minor>.<patch>")
    return m.group(1)


def load_project_version(pyproject_path: str) -> str:
    p = Path(pyproject_path)
    if not p.exists():
        raise ValueError(f"pyproject not found: {pyproject_path}")
    raw = p.read_text(encoding="utf-8")
    if tomllib is not None:
        data = tomllib.loads(raw)
        version = str((data.get("project") or {}).get("version") or "").strip()
    else:
        version = _fallback_read_project_version(raw)
    if not version:
        raise ValueError("project.version is empty in pyproject.toml")
    return version


def _fallback_read_project_version(pyproject_raw: str) -> str:
    in_project = False
    for line in pyproject_raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if in_project:
            m = re.match(r'^version\s*=\s*"([^"]+)"\s*$', stripped)
            if m:
                return m.group(1).strip()
    return ""


def verify_release_tag_matches_project_version(*, tag: str, pyproject_path: str) -> str:
    tag_version = extract_release_version_from_tag(tag)
    project_version = load_project_version(pyproject_path)
    if tag_version != project_version:
        raise ValueError(
            f"release tag version mismatch: tag={tag_version}, project={project_version}"
        )
    return tag_version


def verify_openapi_file(openapi_path: str) -> None:
    p = Path(openapi_path)
    if not p.exists():
        raise ValueError(f"openapi file not found: {openapi_path}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"openapi is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("openapi root must be JSON object")
    if "openapi" not in data:
        raise ValueError("openapi field is missing in spec")
