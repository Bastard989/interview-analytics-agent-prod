"""
Release checks for GitHub release workflow.
"""

from __future__ import annotations

import argparse
import os

from interview_analytics_agent.common.release_policy import (
    verify_openapi_file,
    verify_release_tag_matches_project_version,
)


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate release policy")
    p.add_argument(
        "--tag", default=os.getenv("GITHUB_REF_NAME", ""), help="Release tag (e.g. v0.1.0)"
    )
    p.add_argument("--pyproject", default="pyproject.toml", help="Path to pyproject.toml")
    p.add_argument("--openapi", default="openapi/openapi.json", help="Path to OpenAPI spec")
    return p.parse_args()


def main() -> int:
    args = _args()
    try:
        v = verify_release_tag_matches_project_version(tag=args.tag, pyproject_path=args.pyproject)
        verify_openapi_file(args.openapi)
    except ValueError as e:
        print(f"release-check failed: {e}")
        return 2

    print(f"release-check OK: version={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
