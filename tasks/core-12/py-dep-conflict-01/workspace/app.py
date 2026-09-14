"""Report builder.

Uses the reporting plugin for titles and slugs, and the export plugin for normalised
output with stable identifiers.
"""

from __future__ import annotations

import plugina
import pluginb

ROWS = [
    "  Quarterly   Revenue  ",
    "Customer Churn",
    "  Net  Promoter Score",
]


def build() -> list[str]:
    """Return one report line per row."""
    lines = [f"# {plugina.title('quarterly  report')}"]
    for text, identifier in pluginb.export_with_ids(ROWS):
        lines.append(f"{identifier}  {text}  ({plugina.slug(text)})")
    return lines


if __name__ == "__main__":
    for line in build():
        print(line)
