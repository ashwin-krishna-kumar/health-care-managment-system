"""The website shares its API origin and never exposes unlocked node operations."""
from fastapi.testclient import TestClient
import pytest
from backend.site import app
from backend import site
import httpx


def test_website_and_api_share_an_origin():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "HealthRecords" in response.text
        assert client.get("/api/").json()["message"] == "HealthRecords API is running."
        assert 'const BACKEND_URL = "/api"' in client.get("/app.js").text
        assert client.get("/api/reports").status_code == 401


@pytest.mark.parametrize("method", ["eth_sendTransaction", "eth_accounts", "personal_unlockAccount", "hardhat_impersonateAccount", "hardhat_setCode"])
def test_wallet_relay_rejects_unlocked_and_admin_operations(method):
    with TestClient(app) as client:
        response = client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": []})
        assert response.json()["error"]["code"] == -32601


def test_wallet_relay_rejects_malformed_or_oversized_requests():
    with TestClient(app) as client:
        assert client.post("/rpc", content="{").status_code == 400
        assert client.post("/rpc", json=[]).status_code == 400
        assert client.post("/rpc", content=b"x" * 1_048_577).status_code == 413


def test_wallet_relay_checks_the_actual_node_chain_before_forwarding(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "chain-check", "result": "0x1"})

    original = httpx.AsyncClient
    monkeypatch.setattr(site.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    with TestClient(app) as client:
        response = client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "eth_sendRawTransaction", "params": ["0x00"]})
        assert response.status_code == 403
        assert len(requests) == 1  # Only the chain check reached the configured node.
