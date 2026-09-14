#!/usr/bin/env python3
"""Build the vendored wheelhouse for py-dep-conflict-01.

The task is a real dependency resolution conflict, and it has to be solvable with the
network switched off. So the packages are tiny pure-Python wheels built here and committed
under `workspace/wheelhouse/`. Wheels are constructed directly as zip archives rather than
through a build backend, which means this script needs nothing but the standard library
and produces byte identical output on any machine.

Run it from the task directory when you change a package:

    python3 vendor/build_wheelhouse.py
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import zipfile
from pathlib import Path

WHEELHOUSE = Path(__file__).resolve().parent.parent / "workspace" / "wheelhouse"

# name, version, requires, module source
PACKAGES: list[tuple[str, str, list[str], str]] = [
    (
        "libcore",
        "1.4.0",
        [],
        '''"""Core primitives, 1.x series."""

__version__ = "1.4.0"
API_LEVEL = 1


def normalise(text):
    """Lowercase and strip. The 1.x behaviour, which collapses inner whitespace too."""
    return " ".join(text.lower().split())
''',
    ),
    (
        "libcore",
        "2.1.0",
        [],
        '''"""Core primitives, 2.x series."""

__version__ = "2.1.0"
API_LEVEL = 2


def normalise(text, *, collapse=True):
    """Lowercase and strip, with inner whitespace collapsing now optional."""
    lowered = text.lower().strip()
    return " ".join(lowered.split()) if collapse else lowered


def fingerprint(text):
    """Stable short hash of the normalised text. New in 2.x."""
    import hashlib

    return hashlib.sha256(normalise(text).encode()).hexdigest()[:12]
''',
    ),
    (
        "plugina",
        "1.0.0",
        ["libcore>=1.0,<2.0"],
        '''"""Reporting plugin, built against the libcore 1.x API."""

__version__ = "1.0.0"

from libcore import normalise


def title(text):
    """Return a display title."""
    return normalise(text).title()
''',
    ),
    (
        "plugina",
        "2.0.0",
        ["libcore>=2.0,<3.0"],
        '''"""Reporting plugin, built against the libcore 2.x API."""

__version__ = "2.0.0"

from libcore import fingerprint, normalise


def title(text):
    """Return a display title."""
    return normalise(text).title()


def slug(text):
    """Return a stable slug, using the 2.x fingerprint."""
    return f"{normalise(text).replace(' ', '-')}-{fingerprint(text)}"
''',
    ),
    (
        "pluginb",
        "1.0.0",
        ["libcore>=1.0,<2.0"],
        '''"""Export plugin, built against the libcore 1.x API."""

__version__ = "1.0.0"

from libcore import normalise


def export(rows):
    """Return one normalised line per row."""
    return [normalise(row) for row in rows]
''',
    ),
    (
        "pluginb",
        "3.0.0",
        ["libcore>=2.0,<3.0"],
        '''"""Export plugin, built against the libcore 2.x API."""

__version__ = "3.0.0"

from libcore import fingerprint, normalise


def export(rows):
    """Return one normalised line per row."""
    return [normalise(row) for row in rows]


def export_with_ids(rows):
    """Return normalised lines paired with their fingerprints."""
    return [(normalise(row), fingerprint(row)) for row in rows]
''',
    ),
]


def _urlsafe_digest(data: bytes) -> str:
    """Return the RECORD style hash of a file."""
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def build(name: str, version: str, requires: list[str], source: str) -> Path:
    """Write one wheel and return its path."""
    dist_info = f"{name}-{version}.dist-info"
    metadata_lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        "Summary: Fixture package for the trajectory py-dep-conflict-01 task.",
        "License: Apache-2.0",
        "Requires-Python: >=3.9",
    ]
    metadata_lines += [f"Requires-Dist: {req}" for req in requires]
    metadata = "\n".join(metadata_lines) + "\n"
    wheel_meta = "Wheel-Version: 1.0\nGenerator: trajectory-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"

    members = {
        f"{name}/__init__.py": source,
        f"{dist_info}/METADATA": metadata,
        f"{dist_info}/WHEEL": wheel_meta,
    }

    record_rows = []
    for path, text in members.items():
        data = text.encode()
        record_rows.append([path, _urlsafe_digest(data), str(len(data))])
    record_rows.append([f"{dist_info}/RECORD", "", ""])

    record_buffer = io.StringIO()
    csv.writer(record_buffer, lineterminator="\n").writerows(record_rows)
    members[f"{dist_info}/RECORD"] = record_buffer.getvalue()

    WHEELHOUSE.mkdir(parents=True, exist_ok=True)
    target = WHEELHOUSE / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(members):
            info = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, members[path])
    return target


def main() -> None:
    """Build every wheel."""
    for name, version, requires, source in PACKAGES:
        path = build(name, version, requires, source)
        print(f"built {path.name}")


if __name__ == "__main__":
    main()
