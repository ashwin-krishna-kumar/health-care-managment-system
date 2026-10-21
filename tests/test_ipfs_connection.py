import json
from email.parser import BytesParser
from email import policy

import httpx
import pytest

from backend.check_ipfs import check


@pytest.mark.parametrize("failure", [None, "offline", "corrupt", "oversize"])
def test_connection_roundtrip_and_failure_cleanup(failure):
    state = {"encrypted": None, "unpinned": False}
    cid = "b" + "a" * 58

    def handle(request):
        assert request.method == "POST"
        if request.url.path.endswith("/version"):
            if failure == "offline":
                raise httpx.ConnectError("Node offline", request=request)
            return httpx.Response(200, json={"Version": "test-node"})
        if request.url.path.endswith("/add"):
            envelope = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.content
            payload = next(BytesParser(policy=policy.default).parsebytes(envelope).iter_parts()).get_payload(decode=True)
            assert b"HealthRecords IPFS connection test" not in payload
            assert request.url.params["pin"] == "true"
            state["encrypted"] = payload
            return httpx.Response(200, text=json.dumps({"Hash": cid}) + "\n")
        if request.url.path.endswith("/cat"):
            payload = state["encrypted"]
            if failure == "corrupt":
                payload = b"X" + payload[1:]
            if failure == "oversize":
                payload += b"X"
            return httpx.Response(200, content=payload)
        if request.url.path.endswith("/pin/rm"):
            assert request.url.params["arg"] == cid
            state["unpinned"] = True
            return httpx.Response(200, json={"Pins": [cid]})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        if failure:
            with pytest.raises((httpx.ConnectError, ValueError)):
                check("http://127.0.0.1:5001/api/v0", client)
        else:
            assert check("http://127.0.0.1:5001/api/v0", client) == cid
    assert state["unpinned"] == (failure != "offline")
