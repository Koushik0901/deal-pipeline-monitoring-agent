"""Smoke test: app boots and key routes respond (T033)."""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ["CLOCK_MODE"] = "simulated"
os.environ["ANTHROPIC_API_KEY"] = "test-key"

# Use temp files so sqlite3 connections share state within the test session
_tmp_domain = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_ckpt = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DOMAIN_DB_PATH"] = _tmp_domain.name
os.environ["CHECKPOINT_DB_PATH"] = _tmp_ckpt.name
_tmp_domain.close()
_tmp_ckpt.close()


@pytest.fixture(scope="module")
def client():
    from hiive_monitor.app import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    # Cleanup temp files
    try:
        os.unlink(_tmp_domain.name)
        os.unlink(_tmp_ckpt.name)
    except OSError:
        pass


def test_root_redirects_to_brief(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/brief"


def test_brief_returns_200(client):
    resp = client.get("/brief")
    assert resp.status_code == 200
    assert b"Daily Brief" in resp.content


def test_queue_returns_200(client):
    resp = client.get("/queue")
    assert resp.status_code == 200
    assert b"Open Items" in resp.content


def test_sim_page_returns_200(client):
    resp = client.get("/sim")
    assert resp.status_code == 200
    assert b"Simulation" in resp.content
