"""Allow transcript and summary artifact kinds in meeting_artifacts.

Revision ID: 20260610_0006
Revises: 20260610_0005
"""

from typing import Union

from alembic import op

revision: str = "20260610_0006"
down_revision: Union[str, None] = "20260610_0005"
branch_labels: Union[str, list[str], None] = None
depends_on: Union[str, list[str], None] = None

_ARTIFACT_KINDS = (
    "'recording', 'audio', 'screenshot', 'log', "
    "'transcript', 'transcript_json', 'summary'"
)


def upgrade() -> None:
    op.drop_constraint("ck_meeting_artifacts_kind", "meeting_artifacts", type_="check")
    op.create_check_constraint(
        "ck_meeting_artifacts_kind",
        "meeting_artifacts",
        f"kind IN ({_ARTIFACT_KINDS})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_meeting_artifacts_kind", "meeting_artifacts", type_="check")
    op.create_check_constraint(
        "ck_meeting_artifacts_kind",
        "meeting_artifacts",
        "kind IN ('recording', 'audio', 'screenshot', 'log')",
    )
