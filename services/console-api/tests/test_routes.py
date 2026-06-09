from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("YC_API_KEY", "stub_key_00000000000000000000")
os.environ.setdefault("YC_FOLDER_ID", "b1g0000000000000000")
os.environ.setdefault("TRACKER_TOKEN", "stub_token_000000000000000000000")
os.environ.setdefault("TRACKER_ORG_ID", "000000000000")
os.environ.setdefault("TRACKER_ORG_TYPE", "cloud")
os.environ.setdefault("DEFAULT_TEAM_ID", "00000000-0000-0000-0000-000000000001")


def test_console_api_routes_are_registered() -> None:
    from console_api.main import app

    routes = {(route.path, ",".join(sorted(route.methods or []))) for route in app.routes}

    assert ("/auth/login", "POST") in routes
    assert ("/auth/code/request", "POST") in routes
    assert ("/auth/code/verify", "POST") in routes
    assert ("/auth/logout", "POST") in routes
    assert ("/auth/me", "GET") in routes
    assert ("/agents", "GET") in routes
    assert ("/agents/{name}/config", "GET") in routes
    assert ("/agents/{name}/spec", "PATCH") in routes
    assert ("/agents/{name}/overlay", "PATCH") in routes
    assert ("/actions", "GET") in routes
    assert ("/actions/{action_id}", "GET") in routes
    assert ("/confirms", "GET") in routes
    assert ("/confirms/{confirm_id}/decision", "POST") in routes
    assert ("/playground/{agent}/chat", "POST") in routes
