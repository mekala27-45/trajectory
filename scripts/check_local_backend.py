#!/usr/bin/env python
"""Assert the local sandbox backend can actually run a task's verify command.

A task's verify command is written against the task image, where the Dockerfile installs
whatever the tests need. The local backend has no image: it runs that command against
whichever interpreter the sanitised PATH resolves, which is a property of the machine and
not of this repository.

The tests that drive a verify command through the local backend skip themselves when that
interpreter cannot run `python -m pytest`, so a contributor without it gets a green suite
rather than a confusing failure. That is right for a laptop and wrong for CI, where a
silent skip means a whole class of behaviour stops being covered and nobody notices. So CI
runs this after installing the interpreter's packages, and fails loudly if the install
landed somewhere the sandbox cannot see.

That is exactly what went wrong once: the workflow ran `python3 -m pip install pytest`,
which installed for the runner's toolcache Python, while the sandbox resolved
`/usr/bin/python`. Five tests failed with nothing but `No module named pytest` to go on.

Exits 0 when a verify command is runnable, 1 otherwise, printing the PATH it resolved
against and the interpreter it found so the fix is obvious from the log alone.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from trajectory_runner.sandbox import local_verify_runnable, sanitised_path


def main() -> int:
    """Report whether the local backend can run `python -m pytest`, and where from."""
    path = sanitised_path()
    interpreter = shutil.which("python", path=path)

    print(f"sanitised PATH: {path}")
    print(f"python resolves to: {interpreter or 'nothing on that PATH'}")

    if local_verify_runnable():
        version = subprocess.run(
            # Partial path on purpose: this reports what a task resolves, not what an
            # absolute path would resolve.
            ["python", "-m", "pytest", "--version"],
            env={"PATH": path},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        print(f"verify commands are runnable: {version.stdout.strip() or version.stderr.strip()}")
        return 0

    print(
        "\nthe local backend cannot run `python -m pytest`.\n"
        "Every test marked `local_verify` will skip, which is not acceptable in CI.\n"
        "Install pytest for the interpreter named above, not for whichever `python3` is\n"
        "first on the workflow's own PATH. Those are frequently different on a hosted\n"
        "runner, and that difference is the reason this check exists:\n"
        "\n"
        '    sudo "$(command -v python3)" -m pip install --break-system-packages pytest\n'
        "\n"
        "is wrong on a runner. Name the interpreter explicitly instead.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
