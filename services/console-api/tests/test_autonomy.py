from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("YC_API_KEY", "stub_key_00000000000000000000")
os.environ.setdefault("YC_FOLDER_ID", "b1g0000000000000000")
os.environ.setdefault("TRACKER_TOKEN", "stub_token_000000000000000000000")
os.environ.setdefault("TRACKER_ORG_ID", "000000000000")
os.environ.setdefault("TRACKER_ORG_TYPE", "cloud")
os.environ.setdefault("DEFAULT_TEAM_ID", "00000000-0000-0000-0000-000000000001")

from console_api.main import AutonomyDTO  # noqa: E402


def test_autonomy_accepts_exclusive_risk_groups() -> None:
    autonomy = AutonomyDTO(
        auto_risk=["low"],
        confirm_risk=["medium", "high"],
        always_confirm_tools=[],
    )

    assert autonomy.auto_risk == ["low"]
    assert autonomy.confirm_risk == ["medium", "high"]


def test_autonomy_rejects_overlapping_risk_groups() -> None:
    with pytest.raises(ValidationError) as exc_info:
        AutonomyDTO(
            auto_risk=["low", "medium"],
            confirm_risk=["medium", "high"],
            always_confirm_tools=[],
        )

    assert "auto_risk and confirm_risk must not contain the same risk levels" in str(exc_info.value)
