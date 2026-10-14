#!/usr/bin/env python3

"""Local helpers for the E2E stack that the bublik-e2e CLI does not cover.

Campaign parsing, validation and seeding all live in the ``bublik-e2e`` CLI
(``bublik-e2e plan/generate/run --plan e2e/plan.yaml``). What is left here is
specific to this repository's layout:

``clean``                  remove generated artifacts, refusing paths outside
                           the directories E2E is allowed to write to
``guard-compose-project``  refuse destructive Compose actions when the E2E and
                           production project names collide
``seeded``                 exit 0 when the running stack already has the
                           manifest's runs, so ``task e2e:seed`` can skip
                           reseeding
``record-plan``            remember which plan (or ``--override``) the stack
                           was seeded from, next to the manifest
``check-plan``             fail when the stack is seeded but the plan changed
                           since, printing the commands that reset it
"""

import hashlib
import json
import os
import shlex
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# Playwright writes its reports and traces here, and its auth setup project
# caches a logged-in storage state. Both survive a Compose teardown, so a reset
# has to clear them explicitly.
PLAYWRIGHT_ARTIFACTS = (
    Path("bublik-ui/dist/.playwright"),
    Path("bublik-ui/e2e/.auth"),
)
PROBE_TIMEOUT_SECONDS = 5


class E2EError(ValueError):
    pass


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise E2EError(f"{name} is required")
    return value


def _resolve_allowed(path: Path, allowed_root: Path, kind: str) -> Path:
    """Return ``path`` only if it stays inside ``allowed_root``.

    Every deletion below goes through here first: a typo or a stray symlink in
    BUBLIK_E2E_PUBLISH_DIR must not turn into an rmtree somewhere else.
    """
    candidate = path.expanduser()
    if ".." in candidate.parts:
        raise E2EError(f"{kind} must not contain parent path components: {path}")
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    candidate = candidate.absolute()
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as error:
        raise E2EError(f"cannot resolve {kind}: {candidate}: {error}") from error
    allowed_root = allowed_root.absolute()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as error:
        raise E2EError(
            f"{kind} must resolve under {allowed_root}, got {resolved}"
        ) from error
    return candidate


def _artifact_paths() -> tuple[Path, Path]:
    """The publish directory and manifest, both checked before anything is removed."""
    data_dir = Path(_required_environment("BUBLIK_DOCKER_DATA_DIR"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    try:
        data_dir = data_dir.expanduser().resolve()
    except (OSError, RuntimeError) as error:
        raise E2EError(f"cannot resolve BUBLIK_DOCKER_DATA_DIR: {error}") from error

    publish_root = data_dir / "logs" / "logs" / "e2e"
    publish_dir = _resolve_allowed(
        Path(_required_environment("BUBLIK_E2E_PUBLISH_DIR")),
        publish_root,
        "publish directory",
    )
    manifest_root = ROOT.resolve() / ".e2e"
    manifest = _resolve_allowed(
        Path(_required_environment("BUBLIK_E2E_MANIFEST")),
        manifest_root,
        "manifest",
    )
    if manifest.resolve() == manifest_root:
        raise E2EError("manifest must be a file below ROOT/.e2e")
    if (
        publish_dir.exists()
        and not publish_dir.is_dir()
        and not publish_dir.is_symlink()
    ):
        raise E2EError(f"expected a publish directory: {publish_dir.resolve()}")
    if manifest.exists() and manifest.is_dir() and not manifest.is_symlink():
        raise E2EError(f"expected a manifest file: {manifest.resolve()}")
    return publish_dir, manifest


def _remove_directory(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        if not path.is_dir():
            raise E2EError(f"expected a directory: {path}")
        shutil.rmtree(path)


def clean_artifacts() -> None:
    """Remove generated fixtures, the manifest, and Playwright's output."""
    publish_dir, manifest = _artifact_paths()
    _remove_directory(publish_dir)
    manifest.unlink(missing_ok=True)
    plan_stamp(manifest).unlink(missing_ok=True)
    for relative in PLAYWRIGHT_ARTIFACTS:
        _remove_directory(_resolve_allowed(relative, ROOT.resolve(), "test output"))


def guard_compose_project() -> None:
    normal = _required_environment("BUBLIK_NORMAL_COMPOSE_PROJECT_NAME")
    e2e = _required_environment("BUBLIK_E2E_COMPOSE_PROJECT_NAME")
    if normal == e2e:
        raise E2EError(
            "refusing destructive cleanup because the E2E Compose project "
            f"equals the normal project: {normal!r}"
        )


def _api_bundles(manifest: Path) -> list[dict[str, Any]]:
    """Bundles the CLI is responsible for importing (the +ui ones are Playwright's)."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise E2EError(f"cannot read manifest {manifest}: {error}") from error
    bundles = data.get("bundles")
    if not isinstance(bundles, list):
        raise E2EError("manifest bundles must be an array")
    return [
        bundle
        for bundle in bundles
        if isinstance(bundle, dict) and bundle.get("importVia") != "ui"
    ]


def _run_exists(url: str) -> bool:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def is_seeded() -> bool:
    """True when the running instance already has this manifest's runs.

    A manifest full of run ids is not enough on its own: the database may have
    been wiped underneath it, in which case those ids point at nothing and the
    stack still needs seeding. So one id is probed against the live API.
    """
    manifest = Path(os.environ.get("BUBLIK_E2E_MANIFEST", "")).expanduser()
    if not manifest.is_file():
        return False
    bundles = _api_bundles(manifest)
    if not bundles:
        return False
    run_ids = [bundle.get("runId") for bundle in bundles]
    if any(run_id is None for run_id in run_ids):
        return False
    base_url = _required_environment("BUBLIK_E2E_URL").rstrip("/")
    return _run_exists(f"{base_url}/api/v2/runs/{run_ids[0]}/")


# What a stack seeded from a --day/--runs override records instead of a digest:
# its manifest matches no plan file.
OVERRIDE_STAMP = "override"


def plan_stamp(manifest: Path) -> Path:
    """Where the digest of the plan a stack was seeded from is kept."""
    return manifest.with_name(manifest.name + ".plan-sha256")


def _plan_digest(plan: Path) -> str:
    try:
        return hashlib.sha256(plan.read_bytes()).hexdigest()
    except OSError as error:
        raise E2EError(f"cannot read plan {plan}: {error}") from error


def _manifest_path() -> Path:
    return Path(_required_environment("BUBLIK_E2E_MANIFEST")).expanduser()


def record_plan(plan: Path | None) -> None:
    """Record ``plan``'s digest next to the manifest; ``None`` for an override."""
    stamp = plan_stamp(_manifest_path())
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(
        OVERRIDE_STAMP if plan is None else _plan_digest(plan), encoding="utf-8"
    )


def _reset_commands() -> str:
    def assign(*names: str) -> str:
        return " ".join(
            f"{name}={shlex.quote(os.environ.get(name, ''))}" for name in names
        )

    compose_files = os.environ.get("COMPOSE_FILES", "").strip() or (
        "-f docker-compose.yml -f docker-compose.db.yml"
    )
    return "\n".join(
        [
            f"  {assign('COMPOSE_PROJECT_NAME')} docker compose {compose_files} "
            "down --volumes",
            "  "
            + assign(
                "BUBLIK_DOCKER_DATA_DIR",
                "BUBLIK_E2E_PUBLISH_DIR",
                "BUBLIK_E2E_MANIFEST",
            )
            + " python3 scripts/e2e.py clean",
            "  task e2e:up && task e2e:seed",
        ]
    )


def check_plan(plan: Path) -> str | None:
    """Fail when the seeded stack no longer matches ``plan``.

    A seeded stack skips the runs step, so an edited plan never reaches the
    manifest or the instance; regenerating over a populated stack is not an
    option either. The only way forward is a reset, so say exactly how.
    Returns a warning when the stack predates recording the plan.
    """
    if not is_seeded():
        return None
    stamp = plan_stamp(_manifest_path())
    if not stamp.is_file():
        return (
            f"cannot tell whether {plan} changed since this stack was seeded: "
            "it predates recording the plan. If it did, reset the stack:\n"
            + _reset_commands()
        )
    recorded = stamp.read_text(encoding="utf-8").strip()
    if recorded == _plan_digest(plan):
        return None
    if recorded == OVERRIDE_STAMP:
        reason = (
            f"this stack was seeded from a --day/--runs override, not {plan}, "
            "so its runs and classification do not match the plan"
        )
    else:
        reason = (
            f"{plan} changed since this stack was seeded, so its runs, "
            "issues and trackers no longer match the plan"
        )
    raise E2EError(
        f"{reason}. A seeded stack is never regenerated in place; reset it and "
        "seed again:\n" + _reset_commands()
    )


def main() -> int:
    try:
        command = sys.argv[1] if len(sys.argv) > 1 else ""
        if command == "clean":
            clean_artifacts()
            return 0
        if command == "guard-compose-project":
            guard_compose_project()
            return 0
        if command == "seeded":
            return 0 if is_seeded() else 1
        if command == "record-plan" and len(sys.argv) == 3:
            argument = sys.argv[2]
            record_plan(None if argument == "--override" else Path(argument))
            return 0
        if command == "check-plan" and len(sys.argv) == 3:
            warning = check_plan(Path(sys.argv[2]))
            if warning:
                print(f"e2e: warning: {warning}", file=sys.stderr)
            return 0
        raise E2EError(
            "usage: e2e.py clean | guard-compose-project | seeded"
            " | record-plan <plan>|--override | check-plan <plan>"
        )
    except E2EError as error:
        print(f"e2e: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
