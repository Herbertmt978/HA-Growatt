"""Exercise the trusted Dependabot policy without credentials or mutations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_TEXT = (ROOT / ".github/workflows/dependabot-automerge.yml").read_text(encoding="utf-8")


def _run_script(step_id: str) -> str:
    """Extract one literal workflow run block without third-party YAML packages."""
    lines = WORKFLOW_TEXT.splitlines()
    marker = f"        id: {step_id}"
    try:
        step_index = lines.index(marker)
    except ValueError as error:
        raise AssertionError(f"Workflow has no step with id {step_id!r}") from error

    run_index = next(
        (index for index in range(step_index + 1, len(lines)) if lines[index] == "        run: |"),
        None,
    )
    if run_index is None:
        raise AssertionError(f"Workflow step {step_id!r} has no literal run block")

    body: list[str] = []
    for line in lines[run_index + 1 :]:
        if line and not line.startswith("          "):
            break
        body.append(line[10:] if line else "")
    return "\n".join(body)


POLICY = _run_script("policy")


def review_metadata(decision: str | None, reviews: list[dict[str, object]]) -> str:
    """Encode the minimal GitHub review response consumed by the policy."""
    return json.dumps({"reviewDecision": decision, "reviews": reviews})


def review(author: str, state: str, submitted_at: str) -> dict[str, object]:
    """Build one synthetic submitted review."""
    return {
        "author": {"login": author},
        "state": state,
        "submittedAt": submitted_at,
    }


class AutoMergePolicyTest(unittest.TestCase):
    """Run the actual Bash policy using controlled metadata and file names."""

    def eligible(self, **changes: str) -> bool:
        """Stub `gh` so no GitHub request or mutation can occur."""
        environment = {
            **os.environ,
            "DEPENDENCY_GROUP": "",
            "DEPENDENCY_NAMES": "ruff",
            "DIRECTORY": "/",
            "MAINTAINER_CHANGES": "false",
            "NEW_VERSION": "0.15.22",
            "PACKAGE_ECOSYSTEM": "uv",
            "PR_URL": "https://github.invalid/pull/1",
            "REVIEW_METADATA": review_metadata(None, []),
            "UPDATE_TYPE": "version-update:semver-patch",
            "GH_VIEW_FAIL": "false",
            "CHANGED_FILES": "uv.lock\npyproject.toml",
            **changes,
        }
        bash = shutil.which("bash")
        if os.name == "nt":
            candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
            if candidate.is_file():
                bash = str(candidate)
        if bash is None:
            self.fail("Bash is required to verify the auto-merge policy")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            environment["GITHUB_OUTPUT"] = output.as_posix()
            result = subprocess.run(
                [
                    bash,
                    "-c",
                    "gh() { "
                    'if [[ "$2" == "view" ]]; then '
                    '[[ "$GH_VIEW_FAIL" != "true" ]] || return 1; '
                    'printf "%s" "$REVIEW_METADATA"; '
                    'else printf "%s" "$CHANGED_FILES"; fi; };\n' + POLICY,
                ],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False
            return output.read_text(encoding="utf-8").strip() == "eligible=true"

    def test_only_individual_stable_patch_and_minor_updates_are_eligible(self) -> None:
        for dependency in ("ruff", "pytest"):
            with self.subTest(dependency=dependency):
                self.assertTrue(self.eligible(DEPENDENCY_NAMES=dependency))
                self.assertTrue(
                    self.eligible(
                        DEPENDENCY_NAMES=dependency,
                        UPDATE_TYPE="version-update:semver-minor",
                        NEW_VERSION="9.1.0",
                    )
                )

    def test_packaging_runtime_and_other_packages_stay_manual(self) -> None:
        for dependency in (
            "setuptools",
            "build",
            "jinja2",
            "paho-mqtt",
            "pyserial",
            "tzdata",
            "websockets",
            "ruff,pytest",
            "",
        ):
            with self.subTest(dependency=dependency):
                self.assertFalse(self.eligible(DEPENDENCY_NAMES=dependency))
        self.assertFalse(self.eligible(PACKAGE_ECOSYSTEM="github_actions"))
        self.assertFalse(self.eligible(DIRECTORY="/other"))

    def test_groups_maintainer_changes_and_uncertain_metadata_stay_manual(self) -> None:
        for changes in (
            {"DEPENDENCY_GROUP": "lint"},
            {"MAINTAINER_CHANGES": "true"},
            {"MAINTAINER_CHANGES": ""},
            {"MAINTAINER_CHANGES": "unknown"},
            {"DEPENDENCY_NAMES": ""},
            {"UPDATE_TYPE": ""},
            {"NEW_VERSION": ""},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.eligible(**changes))

    def test_major_prerelease_and_unknown_versions_stay_manual(self) -> None:
        for changes in (
            {"UPDATE_TYPE": "version-update:semver-major", "NEW_VERSION": "10.0.0"},
            {"NEW_VERSION": "0.16.0rc1"},
            {"NEW_VERSION": "0.16.0-beta.1"},
            {"NEW_VERSION": "unknown"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.eligible(**changes))

    def test_unexpected_paths_stay_manual(self) -> None:
        for files in (
            "uv.lock",
            "pyproject.toml",
            "pyproject.toml\nuv.lock\nsrc/ha_growatt/api.py",
            "pyproject.toml\nuv.lock\n.github/workflows/dependabot-automerge.yml",
        ):
            with self.subTest(files=files):
                self.assertFalse(self.eligible(CHANGED_FILES=files))

    def test_change_requests_remain_manual_until_that_reviewer_resolves_them(self) -> None:
        request = review("reviewer-a", "CHANGES_REQUESTED", "2026-09-26T10:00:00Z")
        approval = review("reviewer-a", "APPROVED", "2026-09-26T11:00:00Z")
        other_approval = review("reviewer-b", "APPROVED", "2026-09-26T11:00:00Z")

        self.assertFalse(
            self.eligible(REVIEW_METADATA=review_metadata("CHANGES_REQUESTED", [request]))
        )
        self.assertTrue(
            self.eligible(REVIEW_METADATA=review_metadata("APPROVED", [request, approval]))
        )
        self.assertTrue(self.eligible(REVIEW_METADATA=review_metadata("", [])))
        self.assertFalse(
            self.eligible(REVIEW_METADATA=review_metadata(None, [request, other_approval]))
        )
        self.assertFalse(
            self.eligible(
                REVIEW_METADATA=review_metadata(
                    None,
                    [
                        request,
                        review("reviewer-a", "COMMENTED", "2026-09-26T12:00:00Z"),
                        review("reviewer-a", "PENDING", "2026-09-26T13:00:00Z"),
                    ],
                )
            )
        )
        self.assertTrue(
            self.eligible(
                REVIEW_METADATA=review_metadata(
                    None,
                    [request, review("reviewer-a", "DISMISSED", "2026-09-26T11:00:00Z")],
                )
            )
        )

    def test_review_metadata_failures_stay_manual(self) -> None:
        for changes in (
            {"GH_VIEW_FAIL": "true"},
            {"REVIEW_METADATA": "not-json"},
            {"REVIEW_METADATA": json.dumps({"reviews": []})},
            {"REVIEW_METADATA": review_metadata("UNKNOWN", [])},
            {"REVIEW_METADATA": json.dumps({"reviewDecision": None, "reviews": {}})},
            {
                "REVIEW_METADATA": review_metadata(
                    None,
                    [review("reviewer-a", "UNKNOWN", "2026-09-26T11:00:00Z")],
                )
            },
            {
                "REVIEW_METADATA": review_metadata(
                    None,
                    [
                        review("reviewer-a", "CHANGES_REQUESTED", "2026-09-26T11:00:00Z"),
                        review("reviewer-a", "APPROVED", "2026-09-26T11:00:00Z"),
                    ],
                )
            },
            {
                "REVIEW_METADATA": review_metadata(
                    None,
                    [
                        review("reviewer-a", "CHANGES_REQUESTED", ""),
                        review("reviewer-a", "APPROVED", "2026-09-26T12:00:00Z"),
                    ],
                )
            },
            {
                "REVIEW_METADATA": review_metadata(
                    None,
                    [
                        review("reviewer-a", "CHANGES_REQUESTED", "not-a-timestamp"),
                        review("reviewer-a", "APPROVED", "2026-09-26T12:00:00Z"),
                    ],
                )
            },
            {
                "REVIEW_METADATA": review_metadata(
                    None,
                    [{"state": "COMMENTED"}],
                )
            },
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.eligible(**changes))

    def test_privileged_workflow_never_executes_pr_code_or_bypasses_rules(self) -> None:
        self.assertNotIn("actions/checkout", WORKFLOW_TEXT)
        self.assertIn("contents: write", WORKFLOW_TEXT)
        self.assertIn("pull-requests: write", WORKFLOW_TEXT)
        self.assertIn("--auto --squash --match-head-commit", WORKFLOW_TEXT)
        self.assertNotIn("--admin", WORKFLOW_TEXT)
        self.assertNotIn("gh pr review", WORKFLOW_TEXT)
        self.assertEqual(
            WORKFLOW_TEXT.count(
                "if: github.actor == 'dependabot[bot]' && github.event.pull_request.draft == false"
            ),
            2,
        )
        self.assertIn(
            "dependabot/fetch-metadata@25dd0e34f4fe68f24cc83900b1fe3fe149efef98",
            WORKFLOW_TEXT,
        )
        expected_gate = (
            "github.repository == 'Herbertmt978/HA-Growatt' && "
            "github.event.pull_request.user.login == 'dependabot[bot]' && "
            "github.event.pull_request.base.ref == github.event.repository.default_branch && "
            "github.event.pull_request.head.repo.full_name == github.repository && "
            "startsWith(github.event.pull_request.head.ref, 'dependabot/uv/')"
        )
        gate_start = WORKFLOW_TEXT.index("    if: >-\n")
        gate_end = WORKFLOW_TEXT.index("\n    runs-on:", gate_start)
        gate = " ".join(
            line.strip() for line in WORKFLOW_TEXT[gate_start:gate_end].splitlines()[1:]
        )
        self.assertEqual(gate, expected_gate)


if __name__ == "__main__":
    unittest.main()
