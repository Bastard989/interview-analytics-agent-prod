from __future__ import annotations

import json

import pytest

import interview_analytics_agent.common.release_policy as release_policy
from interview_analytics_agent.common.release_policy import (
    extract_release_version_from_tag,
    load_project_version,
    verify_openapi_file,
    verify_release_tag_matches_project_version,
)


def test_extract_release_version_from_tag() -> None:
    assert extract_release_version_from_tag("v1.2.3") == "1.2.3"
    with pytest.raises(ValueError):
        extract_release_version_from_tag("1.2.3")
    with pytest.raises(ValueError):
        extract_release_version_from_tag("v1.2.3-rc1")


def test_verify_release_tag_matches_project_version() -> None:
    version = verify_release_tag_matches_project_version(
        tag="v0.1.0", pyproject_path="pyproject.toml"
    )
    assert version == "0.1.0"
    with pytest.raises(ValueError):
        verify_release_tag_matches_project_version(tag="v0.1.1", pyproject_path="pyproject.toml")


def test_verify_openapi_file(tmp_path) -> None:
    ok = tmp_path / "openapi.json"
    ok.write_text(json.dumps({"openapi": "3.1.0", "paths": {}}), encoding="utf-8")
    verify_openapi_file(str(ok))

    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_openapi_file(str(bad))


def test_load_project_version_fallback_without_tomllib(monkeypatch, tmp_path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "9.8.7"\n', encoding="utf-8")
    monkeypatch.setattr(release_policy, "tomllib", None)
    assert load_project_version(str(pyproject)) == "9.8.7"
