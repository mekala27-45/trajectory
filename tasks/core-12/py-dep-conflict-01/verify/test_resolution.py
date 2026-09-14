"""Hidden verification suite for py-dep-conflict-01.

Checks four things, so a resolution that merely silences pip does not pass:

* the declared requirements resolve offline from the vendored wheelhouse
* the resolved core library is on the 2.x API, which is what app.py actually needs
* app.py produces the expected report against the resolved environment
* app.py and the wheelhouse were left alone
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

WORKSPACE = Path.cwd()
EXPECTED_APP_SHA = "5275c416700ae39a3b37487fa8fa065877e7f1d69477f686eb28da516b9d45a1"


def _install(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-index",
            "--find-links",
            str(WORKSPACE / "wheelhouse"),
            "--target",
            str(target),
            "-r",
            str(WORKSPACE / "requirements.txt"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )


@pytest.fixture(scope="module")
def installed() -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        result = _install(target)
        if result.returncode != 0:
            pytest.fail(
                "requirements.txt does not resolve offline:\n"
                f"{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
            )
        yield target


def _run_module(target: Path, code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=WORKSPACE,
        env={"PYTHONPATH": str(target), "PATH": "/usr/local/bin:/usr/bin:/bin"},
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return result.stdout


def test_requirements_resolve_offline(installed: Path) -> None:
    assert (installed / "libcore").is_dir()
    assert (installed / "plugina").is_dir()
    assert (installed / "pluginb").is_dir()


def test_resolved_core_is_on_the_2x_api(installed: Path) -> None:
    out = _run_module(
        installed,
        "import json, libcore; print(json.dumps({'v': libcore.__version__, 'api': libcore.API_LEVEL}))",
    )
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["api"] == 2, "app.py needs the 2.x core API"
    assert payload["v"].startswith("2."), payload


def test_plugins_are_the_versions_that_target_the_2x_core(installed: Path) -> None:
    out = _run_module(
        installed,
        "import json, plugina, pluginb; "
        "print(json.dumps({'a': plugina.__version__, 'b': pluginb.__version__}))",
    )
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["a"] == "2.0.0", payload
    assert payload["b"] == "3.0.0", payload


def test_application_produces_the_expected_report(installed: Path) -> None:
    out = _run_module(installed, "import app; print('\\n'.join(app.build()))")
    lines = [line for line in out.strip().splitlines() if line]
    assert lines[0] == "# Quarterly Report"
    assert len(lines) == 4
    assert lines[1].startswith("b63c3f9a2d9f  quarterly revenue")
    assert "(quarterly-revenue-" in lines[1]
    assert lines[3].startswith("a419f8580cee  net promoter score")


def test_the_application_was_not_modified() -> None:
    digest = hashlib.sha256((WORKSPACE / "app.py").read_bytes()).hexdigest()
    assert digest == EXPECTED_APP_SHA, (
        "app.py was modified. The application code is correct, the pins are not."
    )


def test_the_wheelhouse_was_not_modified() -> None:
    names = sorted(p.name for p in (WORKSPACE / "wheelhouse").glob("*.whl"))
    assert names == [
        "libcore-1.4.0-py3-none-any.whl",
        "libcore-2.1.0-py3-none-any.whl",
        "plugina-1.0.0-py3-none-any.whl",
        "plugina-2.0.0-py3-none-any.whl",
        "pluginb-1.0.0-py3-none-any.whl",
        "pluginb-3.0.0-py3-none-any.whl",
    ], names
