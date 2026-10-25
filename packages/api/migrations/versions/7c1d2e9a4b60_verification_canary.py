"""verification canary and tamper flag

The verifier now plants a must fail test among the hidden tests and records its name, and
marks a run whose report could not be believed. Both are kept on the run row so a run
read back through the API says the same thing as its record on disk.

Revision ID: 7c1d2e9a4b60
Revises: 54b824e69d41
Created: 2026-10-25 02:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "7c1d2e9a4b60"
down_revision: str | None = "54b824e69d41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "verification_canary", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True
        ),
    )
    # Every run stored before this revision ran without a planted test, so none of them
    # can have been caught by one.
    op.add_column(
        "runs",
        sa.Column(
            "verification_tampered", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "verification_tampered")
    op.drop_column("runs", "verification_canary")
