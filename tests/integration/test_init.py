"""Integration tests for src/cli/init.py.

Covers Stage B DoD items for `cc-autopipe init`:
- creates .cc-autopipe/ from templates
- --force overwrites existing
- refuses non-empty .cc-autopipe/ without --force
- adds project to projects.list
- writes .claude/settings.json with absolute paths
- adds gitignore entries
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
INIT_PY = SRC / "cli" / "init.py"


def _run_init(
    project: Path,
    user_home: Path,
    *args: str,
    expect_rc: int | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    cp = subprocess.run(
        [sys.executable, str(INIT_PY), *args, str(project)],
        capture_output=True,
        text=True,
        env=env,
    )
    if expect_rc is not None:
        assert cp.returncode == expect_rc, (
            f"expected rc={expect_rc}, got {cp.returncode}\n"
            f"stdout: {cp.stdout}\nstderr: {cp.stderr}"
        )
    return cp


@pytest.fixture
def fresh(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "demo"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    user_home = tmp_path / "user-home"
    return project, user_home


# --- happy path ----------------------------------------------------------


def test_init_creates_full_skeleton(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)

    cca = project / ".cc-autopipe"
    for name in (
        "config.yaml",
        "agents.json",
        "prd.md",
        "context.md",
        "rules.md",
        "verify.sh",
        "state.json",
    ):
        assert (cca / name).exists(), f"missing {name}"

    settings = project / ".claude" / "settings.json"
    assert settings.exists()


def test_settings_json_has_absolute_hook_paths(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)

    raw = json.loads((project / ".claude" / "settings.json").read_text())
    found = []
    for events in raw["hooks"].values():
        for group in events:
            for hook in group["hooks"]:
                found.append(hook["command"])
    assert len(found) == 4
    for cmd in found:
        assert cmd.startswith("/"), f"hook command not absolute: {cmd}"
        assert "${" not in cmd, f"placeholder not substituted: {cmd}"
        assert cmd.startswith(str(SRC) + "/hooks/"), f"unexpected hook path: {cmd}"


def test_state_json_seeded_fresh(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "active"
    assert s["iteration"] == 0
    assert s["session_id"] is None
    assert s["consecutive_failures"] == 0
    assert s["name"] == project.name


def test_verify_sh_is_executable(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    v = project / ".cc-autopipe" / "verify.sh"
    assert os.access(v, os.X_OK), "verify.sh must be executable"
    # And runs cleanly emitting valid JSON.
    cp = subprocess.run([str(v)], capture_output=True, text=True, check=True)
    raw = json.loads(cp.stdout)
    assert raw["passed"] is False
    assert raw["score"] == 0.0
    assert raw["prd_complete"] is False


# --- projects.list -------------------------------------------------------


def test_projects_list_appended(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    listing = (user_home / "projects.list").read_text().splitlines()
    assert str(project.resolve()) in listing


def test_projects_list_idempotent(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    _run_init(project, user_home, "--force", expect_rc=0)
    listing = (user_home / "projects.list").read_text().splitlines()
    assert listing.count(str(project.resolve())) == 1


# --- gitignore -----------------------------------------------------------


def test_gitignore_entries_added(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    gi = (project / ".gitignore").read_text()
    for line in (
        ".cc-autopipe/state.json",
        ".cc-autopipe/lock",
        ".cc-autopipe/checkpoint.md",
        ".cc-autopipe/HUMAN_NEEDED.md",
        ".cc-autopipe/memory/",
        ".claude/settings.json",
        "MEMORY.md",
    ):
        assert line in gi, f"{line!r} missing from .gitignore"


def test_gitignore_idempotent_no_duplicates(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    _run_init(project, user_home, "--force", expect_rc=0)
    gi_lines = (project / ".gitignore").read_text().splitlines()
    assert gi_lines.count(".cc-autopipe/state.json") == 1


def test_gitignore_preserves_existing_content(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    (project / ".gitignore").write_text("# pre-existing\nnode_modules/\n")
    _run_init(project, user_home, expect_rc=0)
    gi = (project / ".gitignore").read_text()
    assert "# pre-existing" in gi
    assert "node_modules/" in gi
    assert ".cc-autopipe/state.json" in gi


# --- refusal & --force ---------------------------------------------------


def test_init_refuses_non_empty_cca_without_force(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    cp = _run_init(project, user_home, expect_rc=1)
    assert "refusing" in cp.stderr.lower() or "force" in cp.stderr.lower()


def test_init_force_overwrites(fresh: tuple[Path, Path]) -> None:
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    # Pollute config.yaml so we can detect overwrite.
    (project / ".cc-autopipe" / "config.yaml").write_text("polluted: true\n")
    _run_init(project, user_home, "--force", expect_rc=0)
    fresh_cfg = (project / ".cc-autopipe" / "config.yaml").read_text()
    assert "polluted" not in fresh_cfg
    assert "schema_version" in fresh_cfg


# --- non-git-repo warning ------------------------------------------------


def test_init_in_non_git_dir_warns_but_succeeds(tmp_path: Path) -> None:
    project = tmp_path / "non-git"
    project.mkdir()
    user_home = tmp_path / "uhome"
    cp = _run_init(project, user_home, expect_rc=0)
    assert "WARNING" in cp.stderr or "not inside a git repo" in cp.stderr
    assert (project / ".cc-autopipe" / "state.json").exists()


# --- via dispatcher ------------------------------------------------------


def test_init_via_bash_dispatcher(fresh: tuple[Path, Path]) -> None:
    """End-to-end: invoke `cc-autopipe init` via the bash helper."""
    project, user_home = fresh
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    cp = subprocess.run(
        [str(SRC / "helpers" / "cc-autopipe"), "init", str(project)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert "cc-autopipe initialized" in cp.stdout
    assert (project / ".cc-autopipe" / "state.json").exists()


# ---------------------------------------------------------------------------
# agents.json structure (Stage I — researcher + reporter)
# ---------------------------------------------------------------------------


def test_init_provisions_v1_subagents(fresh: tuple[Path, Path]) -> None:
    """v1.0 (Stages I + N): a freshly initialised project must include the
    researcher + reporter (Stage I) + improver (Stage N) subagents
    alongside the v0.5 io-worker + verifier — five total."""
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)

    agents = json.loads((project / ".cc-autopipe" / "agents.json").read_text())
    expected = {"io-worker", "verifier", "researcher", "reporter", "improver"}
    assert set(agents.keys()) == expected, f"got {sorted(agents.keys())}"


def test_v1_subagents_have_required_keys(fresh: tuple[Path, Path]) -> None:
    """Each subagent must declare description, prompt, tools, model,
    and maxTurns — those are the minimum keys Claude Code reads when
    discovering project-local agents."""
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    agents = json.loads((project / ".cc-autopipe" / "agents.json").read_text())

    required = {"description", "prompt", "tools", "model", "maxTurns"}
    for name, spec in agents.items():
        missing = required - set(spec.keys())
        assert not missing, f"{name} missing keys: {missing}"
        assert isinstance(spec["tools"], list), f"{name}.tools not a list"
        assert spec["tools"], f"{name}.tools empty"


def test_researcher_uses_websearch_and_writes_research_dir(
    fresh: tuple[Path, Path],
) -> None:
    """Researcher must have WebSearch + Write so it can produce
    research/<topic>.md per SPEC-v1.md §2.2.1."""
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    agents = json.loads((project / ".cc-autopipe" / "agents.json").read_text())

    r = agents["researcher"]
    assert "WebSearch" in r["tools"]
    assert "Write" in r["tools"]
    assert "Read" in r["tools"]
    # research/ output target referenced in the prompt.
    assert "research/" in r["prompt"]


def test_reporter_uses_read_write_and_targets_reports_dir(
    fresh: tuple[Path, Path],
) -> None:
    """Reporter must have Read + Write and produce reports/iteration-NNN.md."""
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    agents = json.loads((project / ".cc-autopipe" / "agents.json").read_text())

    r = agents["reporter"]
    assert set(r["tools"]) >= {"Read", "Write"}
    assert "reports/" in r["prompt"]
    # background flag per SPEC-v1.md §2.2.2.
    assert r.get("background") is True


def test_v1_subagents_dont_break_existing_orchestrator_agents_arg(
    fresh: tuple[Path, Path],
) -> None:
    """The orchestrator passes --agents <agents.json> to claude. Adding
    new entries must not break the JSON validity that argument relies on."""
    project, user_home = fresh
    _run_init(project, user_home, expect_rc=0)
    # If json.load succeeds the file is well-formed.
    agents = json.loads((project / ".cc-autopipe" / "agents.json").read_text())
    assert isinstance(agents, dict)
    # No two entries should share the same description prefix (would
    # confuse Claude's task tool).
    descriptions = [v.get("description", "") for v in agents.values()]
    assert len(set(descriptions)) == len(descriptions), (
        "duplicate subagent descriptions: " + str(descriptions)
    )
