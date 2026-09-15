#!/usr/bin/env python
"""Fail if a multi-stage Dockerfile moves an editable virtualenv to a different prefix.

The bug this exists to stop shipped in the API image and was invisible to every other
gate in this repository. `uv sync` installs the workspace packages as editable, which
records the absolute source path inside the virtualenv:

    $ cat .venv/lib/python3.12/site-packages/_editable_impl_trajectory_api.pth
    /app/packages/api/src

The builder stage used `WORKDIR /build`, so it recorded `/build/packages/api/src`. The
runtime stage copied the virtualenv to `/app/.venv` and the sources to
`/app/packages/api/src`. Nothing in the build noticed. The image built clean in 28
seconds, pushed, and then every container exited immediately on

    ModuleNotFoundError: No module named 'trajectory_api'

with the real cause three stages back in a path that no longer existed. Linting cannot
see it, mypy cannot see it, and a `docker build` succeeds, so only actually starting the
container catches it, which is why it reached a published tag.

The invariant, checked statically so a machine without a Docker daemon still enforces it:

1. A stage that copies a virtualenv from another stage must share that stage's WORKDIR.
2. Any copy of a virtualenv or of a package source tree between stages must land on the
   same absolute path it came from.
3. A stage that receives a virtualenv must smoke test the import, so a future break fails
   the build with a useful message rather than a health check sixty seconds later.

Exits 0 when clean, 1 otherwise, naming the file, the line and the rule.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VENV_SUFFIX = "/.venv"
PACKAGE_SRC = re.compile(r"^/.*/packages/[^/]+/src/?$")
FROM_LINE = re.compile(r"^FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<name>\S+))?\s*$", re.IGNORECASE)
WORKDIR_LINE = re.compile(r"^WORKDIR\s+(?P<path>\S+)\s*$", re.IGNORECASE)
COPY_FROM_LINE = re.compile(r"^COPY\s+(?P<rest>.*--from=\S+.*)$", re.IGNORECASE)
IMPORT_SMOKE = re.compile(r"^RUN\s+python\s+-c\s+.*\bimport\b", re.IGNORECASE)


@dataclass
class Copy:
    """One `COPY --from=<stage>` instruction, already stripped of its flags."""

    line: int
    source_stage: str
    source: str
    destination: str


@dataclass
class Stage:
    """One `FROM` block: its name, its working directory, and what it copies in."""

    name: str
    image: str
    start_line: int
    workdir: str | None = None
    copies: list[Copy] = field(default_factory=list)
    smoke_tests_imports: bool = False


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Join backslash continuations, dropping comments, keeping the starting line number."""
    joined: list[tuple[int, str]] = []
    buffer = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if not buffer:
            start = number
        if stripped.endswith("\\"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        joined.append((start, (buffer + stripped).strip()))
        buffer = ""
    if buffer:
        joined.append((start, buffer.strip()))
    return joined


def parse_stages(text: str) -> list[Stage]:
    """Split a Dockerfile into its stages, recording WORKDIR and cross stage copies."""
    stages: list[Stage] = []
    for number, line in logical_lines(text):
        if match := FROM_LINE.match(line):
            stages.append(
                Stage(
                    name=match.group("name") or f"stage-{len(stages)}",
                    image=match.group("image"),
                    start_line=number,
                )
            )
            continue
        if not stages:
            continue
        current = stages[-1]
        if match := WORKDIR_LINE.match(line):
            current.workdir = match.group("path")
        elif IMPORT_SMOKE.match(line):
            current.smoke_tests_imports = True
        elif match := COPY_FROM_LINE.match(line):
            words = [w for w in match.group("rest").split() if not w.startswith("--")]
            source_stage = ""
            for word in match.group("rest").split():
                if word.startswith("--from="):
                    source_stage = word.removeprefix("--from=")
            if len(words) >= 2:
                current.copies.append(
                    Copy(
                        line=number,
                        source_stage=source_stage,
                        source=words[-2],
                        destination=words[-1],
                    )
                )
    return stages


def path_is_coupled(path: str) -> bool:
    """Whether this path is one the virtualenv records, so moving it breaks imports."""
    trimmed = path.rstrip("/")
    return trimmed.endswith(VENV_SUFFIX) or bool(PACKAGE_SRC.match(path))


def check(path: Path) -> list[str]:
    """Every rule violation in one Dockerfile, as printable lines."""
    text = path.read_text(encoding="utf-8")
    stages = parse_stages(text)
    by_name = {stage.name: stage for stage in stages}
    relative = path.relative_to(ROOT)
    problems: list[str] = []

    for stage in stages:
        internal = [c for c in stage.copies if c.source_stage in by_name]
        for copy in internal:
            if path_is_coupled(copy.source) and copy.source.rstrip("/") != copy.destination.rstrip(
                "/"
            ):
                problems.append(
                    f"{relative}:{copy.line}: stage `{stage.name}` copies {copy.source} to "
                    f"{copy.destination}. An editable virtualenv records absolute source "
                    f"paths, so this image builds and then cannot import its own code. "
                    f"Copy it to {copy.source} instead."
                )

        venv_copies = [c for c in internal if c.source.rstrip("/").endswith(VENV_SUFFIX)]
        if not venv_copies:
            continue

        for copy in venv_copies:
            source_stage = by_name[copy.source_stage]
            if source_stage.workdir != stage.workdir:
                problems.append(
                    f"{relative}:{copy.line}: stage `{stage.name}` has WORKDIR "
                    f"{stage.workdir} but takes a virtualenv from `{source_stage.name}`, "
                    f"whose WORKDIR is {source_stage.workdir}. The editable install paths "
                    f"were recorded against the latter. Use one WORKDIR for both stages."
                )

        if not stage.smoke_tests_imports:
            problems.append(
                f"{relative}:{stage.start_line}: stage `{stage.name}` receives a "
                f"virtualenv but never imports from it at build time. Add a "
                f'`RUN python -c "import <your package>"` so a path break fails the '
                f"build rather than a health check."
            )

    return problems


def dockerfiles() -> list[Path]:
    """Every tracked Dockerfile that copies anything between stages."""
    found = [p for p in ROOT.rglob("Dockerfile*") if ".git" not in p.parts and p.is_file()]
    return sorted(p for p in found if "--from=" in p.read_text(encoding="utf-8"))


def main() -> int:
    """Check every multi-stage Dockerfile and report each violation."""
    problems: list[str] = []
    checked = 0
    for path in dockerfiles():
        checked += 1
        problems.extend(check(path))

    for problem in problems:
        print(problem, file=sys.stderr)

    if problems:
        print(
            f"\n{len(problems)} problem(s) in {checked} multi-stage Dockerfile(s). "
            "See the header of scripts/check_container_paths.py for why this matters.",
            file=sys.stderr,
        )
        return 1

    print(f"{checked} multi-stage Dockerfile(s), virtualenv paths consistent across stages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
