"""Same-origin website, API, and a limited relay for wallet-signed local transactions."""
import json
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.main import app as api, RPC_URL, EXPECTED_CHAIN_ID

app = FastAPI(title="HealthRecords", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/api", api)

# Never relay unlocked-account transactions or development/admin RPC methods.
WALLET_METHODS = {
    "eth_chainId", "net_version", "web3_clientVersion", "eth_blockNumber", "eth_call",
    "eth_estimateGas", "eth_gasPrice", "eth_maxPriorityFeePerGas", "eth_feeHistory",
    "eth_getBalance", "eth_getCode", "eth_getLogs", "eth_getStorageAt",
    "eth_getBlockByNumber", "eth_getBlockByHash", "eth_getTransactionByHash",
    "eth_getTransactionReceipt", "eth_getTransactionCount", "eth_sendRawTransaction",
}


@app.post("/rpc")
async def wallet_rpc(request: Request):
    # This relay is solely for the local test chain; never attach it to mainnet.
    if EXPECTED_CHAIN_ID != 31337:
        return JSONResponse({"detail": "Local wallet relay is disabled for this chain."}, status_code=403)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1_048_576:
            return JSONResponse({"detail": "RPC request is too large."}, status_code=413)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return JSONResponse({"error": {"code": -32700, "message": "Invalid JSON"}}, status_code=400)
    batch = payload if isinstance(payload, list) else [payload]
    if not 1 <= len(batch) <= 50:
        return JSONResponse({"detail": "Invalid RPC batch size."}, status_code=400)
    for item in batch:
        if not isinstance(item, dict) or item.get("jsonrpc") != "2.0" or item.get("method") not in WALLET_METHODS:
            return JSONResponse({"jsonrpc": "2.0", "id": item.get("id") if isinstance(item, dict) else None,
                                 "error": {"code": -32601, "message": "RPC method is not available through the wallet relay."}})
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
            chain = await client.post(RPC_URL, json={"jsonrpc": "2.0", "id": "chain-check", "method": "eth_chainId", "params": []})
            chain.raise_for_status()
            if chain.json().get("result") != "0x7a69":
                return JSONResponse({"detail": "The wallet relay requires the local development chain (31337)."}, status_code=403)
            response = await client.post(RPC_URL, json=payload)
            response.raise_for_status()
            return JSONResponse(response.json())
    except (httpx.HTTPError, ValueError):
        return JSONResponse({"detail": "Local blockchain is unavailable."}, status_code=503)


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "frontend", html=True), name="site")
