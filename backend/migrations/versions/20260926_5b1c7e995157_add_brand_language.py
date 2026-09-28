"""add brand language

Revision ID: 5b1c7e995157
Revises: a621728cf8cb
Create Date: 2026-09-26 10:11:11.138239
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '5b1c7e995157'
down_revision: str | None = 'a621728cf8cb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing brands keep writing in English.
    op.add_column('brands', sa.Column('language', sa.String(length=8), server_default='en', nullable=False))
    op.create_check_constraint(op.f('ck_brands_language_known'), 'brands', "language IN ('en', 'bn')")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_brands_language_known'), 'brands', type_='check')
    op.drop_column('brands', 'language')
