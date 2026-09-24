"""Persist registry icons.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("mcp_servers", sa.Column("icon_mime", sa.String(32)))
    op.add_column("mcp_servers", sa.Column("icon_data", sa.LargeBinary()))


def downgrade():
    op.drop_column("mcp_servers", "icon_data")
    op.drop_column("mcp_servers", "icon_mime")