"""Acceptance checklist item 14 (partial, Phase 1 surface): secrets absent from API output and logs."""

import logging
import subprocess
from pathlib import Path

from geopoc.config import REPO_ROOT, get_settings


def test_database_password_never_appears_in_responses_logs_or_settings_repr(client, headers, caplog):
    secret = get_settings().db_password.get_secret_value()
    assert secret, "the test needs a real password from .env to be meaningful"

    caplog.set_level(logging.DEBUG)
    bodies = [
        client.get("/health").text,
        client.get("/health/models").text,
        client.get("/openapi.json").text,
        client.get("/api/v1/workspace/profiles").text,
        client.post("/api/v1/projects", json={"name": "s"}, headers=headers).text,
        client.get("/api/v1/projects/not-a-uuid").text,
    ]
    for body in bodies:
        assert secret not in body
    assert secret not in caplog.text
    assert secret not in repr(get_settings())
    assert secret not in str(get_settings().database_url())


def test_env_file_is_git_ignored_and_example_has_no_password():
    result = subprocess.run(
        ["git", "check-ignore", ".env"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, ".env must be ignored by git"
    example = Path(REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GEOPOC_DB_PASSWORD=\n" in example
