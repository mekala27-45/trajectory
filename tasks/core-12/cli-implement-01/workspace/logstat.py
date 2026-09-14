#!/usr/bin/env python3
"""logstat: filter log records by severity and print one of three summaries.

Nothing here is implemented. SPEC.md is the interface, down to the column widths and the
exit codes. Keep this file at this path and keep it runnable as `python3 logstat.py`.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    """Run the tool and return the process exit code.

    Args:
        argv: Command line arguments with the program name already removed.

    Returns:
        The exit code, as specified in SPEC.md.
    """
    raise NotImplementedError("logstat is not implemented. See SPEC.md.")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
