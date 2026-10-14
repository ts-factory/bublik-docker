import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import e2e


class CleanupSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.data_dir = self.root / "data" / "e2e"
        self.publish_root = self.data_dir / "logs" / "logs" / "e2e"
        self.manifest_root = self.root / ".e2e"
        self.publish_root.mkdir(parents=True)
        self.manifest_root.mkdir()
        self.environment = {
            "BUBLIK_DOCKER_DATA_DIR": str(self.data_dir),
            "BUBLIK_E2E_PUBLISH_DIR": str(self.publish_root / "campaign"),
            "BUBLIK_E2E_MANIFEST": str(self.manifest_root / "manifest.json"),
        }
        self.root_patch = patch.object(e2e, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self) -> None:
        self.root_patch.stop()
        self.temporary.cleanup()

    def clean(self, **overrides: str) -> None:
        environment = {**self.environment, **overrides}
        with patch.dict(os.environ, environment, clear=True):
            e2e.clean_artifacts()

    def test_removes_only_allowed_artifacts(self) -> None:
        publish_dir = Path(self.environment["BUBLIK_E2E_PUBLISH_DIR"])
        manifest = Path(self.environment["BUBLIK_E2E_MANIFEST"])
        sibling = self.data_dir / "keep.txt"
        publish_dir.mkdir()
        (publish_dir / "bundle.tar").write_text("fixture", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")
        e2e.plan_stamp(manifest).write_text("0" * 64, encoding="utf-8")
        sibling.write_text("keep", encoding="utf-8")

        self.clean()

        self.assertFalse(publish_dir.exists())
        self.assertFalse(manifest.exists())
        self.assertEqual(sibling.read_text(encoding="utf-8"), "keep")
        self.assertFalse(e2e.plan_stamp(manifest).exists())

    def test_removes_playwright_reports_and_auth_state(self) -> None:
        reports = self.root / "bublik-ui" / "dist" / ".playwright" / "apps" / "bublik"
        auth = self.root / "bublik-ui" / "e2e" / ".auth"
        reports.mkdir(parents=True)
        auth.mkdir(parents=True)
        (reports / "index.html").write_text("report", encoding="utf-8")
        (auth / "state.json").write_text("{}", encoding="utf-8")
        sources = self.root / "bublik-ui" / "apps"
        sources.mkdir(parents=True)
        (sources / "keep.ts").write_text("keep", encoding="utf-8")

        self.clean()

        self.assertFalse((self.root / "bublik-ui" / "dist" / ".playwright").exists())
        self.assertFalse(auth.exists())
        self.assertEqual((sources / "keep.ts").read_text(encoding="utf-8"), "keep")

    def test_missing_playwright_output_is_not_an_error(self) -> None:
        self.clean()

    def test_rejects_outside_publish_path_before_deleting_manifest(self) -> None:
        manifest = Path(self.environment["BUBLIK_E2E_MANIFEST"])
        manifest.write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(e2e.E2EError, "publish directory"):
            self.clean(BUBLIK_E2E_PUBLISH_DIR=str(self.root / "outside"))

        self.assertTrue(manifest.exists())

    def test_rejects_outside_manifest_before_deleting_publish_dir(self) -> None:
        publish_dir = Path(self.environment["BUBLIK_E2E_PUBLISH_DIR"])
        publish_dir.mkdir()

        with self.assertRaisesRegex(e2e.E2EError, "manifest"):
            self.clean(BUBLIK_E2E_MANIFEST=str(self.root / "outside.json"))

        self.assertTrue(publish_dir.exists())

    def test_rejects_parent_path_components(self) -> None:
        with self.assertRaisesRegex(e2e.E2EError, "parent path components"):
            self.clean(BUBLIK_E2E_MANIFEST=str(self.manifest_root / "sub" / ".." / "x"))

    def test_rejects_publish_symlink_escape(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        link = self.publish_root / "link"
        link.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(e2e.E2EError, "publish directory"):
            self.clean(BUBLIK_E2E_PUBLISH_DIR=str(link))

        self.assertTrue(outside.exists())
        self.assertTrue(link.is_symlink())

    def test_rejects_manifest_symlink_escape(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text("keep", encoding="utf-8")
        link = self.manifest_root / "manifest.json"
        link.symlink_to(outside)

        with self.assertRaisesRegex(e2e.E2EError, "manifest"):
            self.clean(BUBLIK_E2E_MANIFEST=str(link))

        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
        self.assertTrue(link.is_symlink())

    def test_requires_the_artifact_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(e2e.E2EError, "BUBLIK_DOCKER_DATA_DIR"):
                e2e.clean_artifacts()


class SeededProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.manifest = Path(self.temporary.name) / "manifest.json"
        self.environment = {
            "BUBLIK_E2E_MANIFEST": str(self.manifest),
            "BUBLIK_E2E_URL": "http://127.0.0.1:42000",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_manifest(self, bundles: list[dict[str, object]]) -> None:
        self.manifest.write_text(json.dumps({"bundles": bundles}), encoding="utf-8")

    def is_seeded(self, run_exists: bool = True) -> bool:
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch.object(e2e, "_run_exists", return_value=run_exists) as probe,
        ):
            result = e2e.is_seeded()
        self.probe = probe
        return result

    def test_seeded_when_every_api_bundle_has_a_live_run(self) -> None:
        self.write_manifest(
            [
                {"id": "a", "runId": 1},
                {"id": "b", "runId": 2},
                {"id": "c", "runId": None, "importVia": "ui"},
            ]
        )

        self.assertTrue(self.is_seeded())
        self.probe.assert_called_once_with("http://127.0.0.1:42000/api/v2/runs/1/")

    def test_not_seeded_without_a_manifest(self) -> None:
        self.assertFalse(self.is_seeded())

    def test_not_seeded_when_an_api_bundle_was_never_imported(self) -> None:
        self.write_manifest([{"id": "a", "runId": 1}, {"id": "b", "runId": None}])

        self.assertFalse(self.is_seeded())

    def test_not_seeded_when_only_ui_bundles_are_planned(self) -> None:
        self.write_manifest([{"id": "a", "runId": None, "importVia": "ui"}])

        self.assertFalse(self.is_seeded())

    def test_not_seeded_when_the_database_was_wiped_underneath(self) -> None:
        """Stale run ids must not skip a seed the stack still needs."""
        self.write_manifest([{"id": "a", "runId": 7}])

        self.assertFalse(self.is_seeded(run_exists=False))

    def test_unreadable_manifest_is_an_error(self) -> None:
        self.manifest.write_text("not json", encoding="utf-8")

        with patch.dict(os.environ, self.environment, clear=True):
            with self.assertRaisesRegex(e2e.E2EError, "cannot read manifest"):
                e2e.is_seeded()

    def test_probe_treats_a_failed_request_as_missing(self) -> None:
        with patch.object(e2e.urllib.request, "urlopen", side_effect=OSError("boom")):
            self.assertFalse(e2e._run_exists("http://127.0.0.1/api/v2/runs/1/"))

    def test_probe_accepts_a_2xx_response(self) -> None:
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(e2e.urllib.request, "urlopen", return_value=response):
            self.assertTrue(e2e._run_exists("http://127.0.0.1/api/v2/runs/1/"))


class PlanDriftTests(unittest.TestCase):
    """A seeded stack must fail loudly once e2e/plan.yaml no longer matches it."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        self.manifest = directory / "manifest.json"
        self.plan = directory / "plan.yaml"
        self.plan.write_text("version: 1\n", encoding="utf-8")
        self.manifest.write_text(
            json.dumps({"bundles": [{"id": "a", "runId": 1}]}), encoding="utf-8"
        )
        self.environment = {
            "BUBLIK_E2E_MANIFEST": str(self.manifest),
            "BUBLIK_E2E_URL": "http://127.0.0.1:42000",
            "COMPOSE_PROJECT_NAME": "bublik-e2e",
            "COMPOSE_FILES": "-f docker-compose.yml -f docker-compose.db.yml",
            "BUBLIK_DOCKER_DATA_DIR": "data/e2e",
            "BUBLIK_E2E_PUBLISH_DIR": "data/e2e/logs/logs/e2e",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_command(self, *args: str, seeded: bool = True) -> tuple[int, str]:
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch.object(e2e, "_run_exists", return_value=seeded),
            patch.object(sys, "argv", ["e2e.py", *args]),
            contextlib.redirect_stderr(stderr),
        ):
            code = e2e.main()
        return code, stderr.getvalue()

    def test_an_unchanged_plan_passes(self) -> None:
        self.assertEqual(self.run_command("record-plan", str(self.plan))[0], 0)

        self.assertEqual(self.run_command("check-plan", str(self.plan)), (0, ""))

    def test_an_edited_plan_fails_with_the_reset_commands(self) -> None:
        self.run_command("record-plan", str(self.plan))
        self.plan.write_text("version: 1\nruns: 2\n", encoding="utf-8")

        code, message = self.run_command("check-plan", str(self.plan))

        self.assertEqual(code, 2)
        self.assertIn(f"{self.plan} changed since this stack was seeded", message)
        self.assertIn(
            "COMPOSE_PROJECT_NAME=bublik-e2e docker compose "
            "-f docker-compose.yml -f docker-compose.db.yml down --volumes",
            message,
        )
        self.assertIn(
            "BUBLIK_DOCKER_DATA_DIR=data/e2e "
            "BUBLIK_E2E_PUBLISH_DIR=data/e2e/logs/logs/e2e "
            f"BUBLIK_E2E_MANIFEST={self.manifest} python3 scripts/e2e.py clean",
            message,
        )
        self.assertIn("task e2e:up && task e2e:seed", message)

    def test_a_stack_seeded_from_an_override_counts_as_drift(self) -> None:
        self.run_command("record-plan", "--override")

        code, message = self.run_command("check-plan", str(self.plan))

        self.assertEqual(code, 2)
        self.assertIn("seeded from a --day/--runs override", message)

    def test_an_unseeded_stack_has_nothing_to_drift_from(self) -> None:
        self.run_command("record-plan", str(self.plan))
        self.plan.write_text("version: 1\nruns: 2\n", encoding="utf-8")

        self.assertEqual(self.run_command("check-plan", str(self.plan), seeded=False), (0, ""))

    def test_a_stack_seeded_before_plans_were_recorded_only_warns(self) -> None:
        code, message = self.run_command("check-plan", str(self.plan))

        self.assertEqual(code, 0)
        self.assertIn("cannot tell whether", message)
        self.assertIn("down --volumes", message)


class ComposeGuardTests(unittest.TestCase):
    def test_rejects_normal_project_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BUBLIK_NORMAL_COMPOSE_PROJECT_NAME": "bublik",
                "BUBLIK_E2E_COMPOSE_PROJECT_NAME": "bublik",
            },
            clear=True,
        ):
            with self.assertRaises(e2e.E2EError):
                e2e.guard_compose_project()

    def test_accepts_distinct_project_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BUBLIK_NORMAL_COMPOSE_PROJECT_NAME": "bublik",
                "BUBLIK_E2E_COMPOSE_PROJECT_NAME": "bublik-e2e",
            },
            clear=True,
        ):
            e2e.guard_compose_project()


if __name__ == "__main__":
    unittest.main()
