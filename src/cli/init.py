#!/usr/bin/env python3
"""init.py — implements `cc-autopipe init` per SPEC.md §12.1.

Copies the project skeleton from templates/.cc-autopipe/ into the
current working directory, writes .claude/settings.json with absolute
paths to the engine's hook scripts, registers the project in
~/.cc-autopipe/projects.list, appends gitignore entries, and seeds a
fresh state.json.

Refs: SPEC.md §5.3, §7.5, §7.7, §7.8, §12.1
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import string
import sys
from pathlib import Path

# Import sibling state.py for State dataclass + atomic write.
_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))
import state  # noqa: E402

# .gitignore entries from SPEC.md §5.3 — engine-managed files that must
# never be committed by the project repo.
GITIGNORE_ENTRIES = [
    ".cc-autopipe/state.json",
    ".cc-autopipe/lock",
    ".cc-autopipe/checkpoint.md",
    ".cc-autopipe/HUMAN_NEEDED.md",
    ".cc-autopipe/memory/",
    ".claude/settings.json",
    "MEMORY.md",
]

# Files in templates/ to copy AS-IS (no name change).
COPY_AS_IS = {"config.yaml", "agents.json"}

# Suffixes stripped on copy: foo.md.example -> foo.md.
STRIPPED_SUFFIXES = (".example", ".template")


def _engine_home() -> Path:
    """Resolve CC_AUTOPIPE_HOME, falling back to the parent of this file's parent.

    Dev: parent of src/cli/ is src/ — that's the engine home in dev.
    Installed: $CC_AUTOPIPE_HOME or ~/cc-autopipe.
    """
    env = os.environ.get("CC_AUTOPIPE_HOME")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent  # src/


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _is_git_repo(p: Path) -> bool:
    cur = p.resolve()
    while True:
        if (cur / ".git").exists():
            return True
        if cur.parent == cur:
            return False
        cur = cur.parent


def _strip_template_suffix(name: str) -> str:
    for suf in STRIPPED_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _copy_templates(src_templates: Path, dst_cca: Path) -> list[str]:
    """Copy templates/.cc-autopipe/ into dst_cca, stripping template suffixes.

    Returns the list of destination filenames written.
    Excludes settings.json.template (handled separately).
    """
    written: list[str] = []
    dst_cca.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src_templates.iterdir()):
        if entry.name == "settings.json.template":
            continue  # written into .claude/, not .cc-autopipe/
        if entry.is_dir():
            continue
        dst_name = (
            entry.name
            if entry.name in COPY_AS_IS
            else _strip_template_suffix(entry.name)
        )
        dst = dst_cca / dst_name
        shutil.copy2(entry, dst)
        if dst_name.endswith(".sh"):
            os.chmod(dst, 0o755)
        written.append(dst_name)
    return written


def _write_settings_json(template_path: Path, target: Path, engine_home: Path) -> None:
    """Render settings.json.template with ${CC_AUTOPIPE_HOME} substitution."""
    raw = template_path.read_text(encoding="utf-8")
    rendered = string.Template(raw).safe_substitute(CC_AUTOPIPE_HOME=str(engine_home))
    # Validate JSON before writing — typos in the template would otherwise
    # ship a broken settings.json that breaks Claude Code session bootstrap.
    json.loads(rendered)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")


def _append_to_projects_list(user_home: Path, project_path: Path) -> bool:
    """Append project absolute path to ~/.cc-autopipe/projects.list (idempotent).

    Returns True if added, False if already present.
    """
    user_home.mkdir(parents=True, exist_ok=True)
    list_path = user_home / "projects.list"
    abs_path = str(project_path.resolve())
    existing: list[str] = []
    if list_path.exists():
        existing = [
            ln.strip() for ln in list_path.read_text().splitlines() if ln.strip()
        ]
    if abs_path in existing:
        return False
    with list_path.open("a", encoding="utf-8") as f:
        f.write(abs_path + "\n")
    return True


def _ensure_gitignore_entries(project: Path) -> list[str]:
    """Append missing engine entries to project/.gitignore. Returns added lines."""
    gi = project / ".gitignore"
    existing: set[str] = set()
    if gi.exists():
        existing = {ln.strip() for ln in gi.read_text().splitlines() if ln.strip()}
    to_add = [e for e in GITIGNORE_ENTRIES if e not in existing]
    if not to_add:
        return []
    header = "\n# cc-autopipe (engine-managed, never commit)\n"
    body = "".join(line + "\n" for line in to_add)
    with gi.open("a", encoding="utf-8") as f:
        if gi.exists() and gi.stat().st_size > 0:
            f.write(header)
        else:
            f.write(header.lstrip("\n"))
        f.write(body)
    return to_add


def _seed_state_json(project: Path) -> None:
    s = state.State.fresh(project.name)
    state.write(project, s)


# v1.3.3 Group N: knowledge.md is enforced by `cc-autopipe-detach`. Seed
# the file with a structured header so Claude has a clear template to
# append to. Existing files are left alone — operators with curated
# notes don't get clobbered.
KNOWLEDGE_HEADER = """# Project Knowledge

Append entries chronologically. Each entry MUST follow this format:

## YYYY-MM-DD HH:MM UTC — <task_id> — <REJECT|ACCEPT|INFRA_FAILED>

**What was tested:** <one-line summary>

**Outcome:** <metric or failure mode>

**Lesson:** <what to do/avoid in future tasks>

**Artifacts:** <paths to relevant logs/models/reports>

"""


def _seed_knowledge_md(project: Path) -> bool:
    """Create .cc-autopipe/knowledge.md with the v1.3.3 header if missing.

    Returns True iff a new file was written. Existing files are not
    overwritten — operators may have curated notes.
    """
    knowledge = project / ".cc-autopipe" / "knowledge.md"
    if knowledge.exists():
        return False
    knowledge.parent.mkdir(parents=True, exist_ok=True)
    knowledge.write_text(KNOWLEDGE_HEADER, encoding="utf-8")
    return True


def init(project: Path, force: bool = False) -> int:
    project = project.resolve()
    cca = project / ".cc-autopipe"
    engine_home = _engine_home()
    templates = engine_home / "templates" / ".cc-autopipe"

    if not templates.is_dir():
        print(
            f"cc-autopipe init: templates not found at {templates}. "
            f"Set CC_AUTOPIPE_HOME or run install.sh.",
            file=sys.stderr,
        )
        return 2

    if cca.exists() and any(cca.iterdir()):
        if not force:
            print(
                f"cc-autopipe init: refusing — {cca} is not empty. "
                f"Use --force to overwrite.",
                file=sys.stderr,
            )
            return 1
        # --force: wipe existing .cc-autopipe/ before re-copying templates.
        shutil.rmtree(cca)

    if not _is_git_repo(project):
        print(
            f"cc-autopipe init: WARNING — {project} is not inside a git repo. "
            f"Continuing anyway.",
            file=sys.stderr,
        )

    written = _copy_templates(templates, cca)

    settings_target = project / ".claude" / "settings.json"
    _write_settings_json(
        templates / "settings.json.template", settings_target, engine_home
    )

    _seed_state_json(project)
    knowledge_seeded = _seed_knowledge_md(project)

    user_home = _user_home()
    added_to_list = _append_to_projects_list(user_home, project)

    gitignore_added = _ensure_gitignore_entries(project)

    print("✓ cc-autopipe initialized")
    print(f"  project:        {project}")
    print(f"  template files: {', '.join(written)}")
    print(f"  settings.json:  {settings_target}")
    print(
        f"  projects.list:  {'added' if added_to_list else 'already present'} "
        f"({user_home / 'projects.list'})"
    )
    print(
        f"  .gitignore:     {len(gitignore_added)} entries added"
        if gitignore_added
        else "  .gitignore:     already up to date"
    )
    print(
        "  knowledge.md:   seeded with v1.3.3 header"
        if knowledge_seeded
        else "  knowledge.md:   already present"
    )
    print()
    print("Next steps:")
    print("  1. Edit .cc-autopipe/prd.md         (define what to build)")
    print("  2. Edit .cc-autopipe/context.md     (stack, constraints)")
    print("  3. Edit .cc-autopipe/verify.sh      (chmod +x, return §7.7 JSON)")
    print("  4. Edit .cc-autopipe/rules.md       (project-specific rules)")
    print("  5. Test:    cc-autopipe run . --once")
    print("  6. Run:     cc-autopipe start")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cc-autopipe init")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing .cc-autopipe/",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="project root (default: current directory)",
    )
    args = parser.parse_args(argv)
    return init(Path(args.path), force=args.force)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
