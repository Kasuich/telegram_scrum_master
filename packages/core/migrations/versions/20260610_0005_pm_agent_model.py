"""Switch the default PM agent model to gpt-oss-120b.

Revision ID: 20260610_0005
Revises: 20260609_0004
"""

from typing import Union

from alembic import op

revision: str = "20260610_0005"
down_revision: Union[str, None] = "20260609_0004"
branch_labels: Union[str, list[str], None] = None
depends_on: Union[str, list[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE agent_specs
        SET model = 'gpt-oss-120b'
        WHERE name = 'pm_agent' AND model = 'yandexgpt'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE agent_specs
        SET model = 'yandexgpt'
        WHERE name = 'pm_agent' AND model = 'gpt-oss-120b'
        """
    )
