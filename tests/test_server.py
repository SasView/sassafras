"""HTTP smoke test: start the server in a thread and exercise each endpoint."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sassafras.backend import Session
from sassafras.server import make_handler

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sphere_hardsphere.xml"
TOKEN = "test-token"


@pytest.fixture(scope="module")
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Session(), TOKEN, {"model": None, "messages": []}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def post(base, path, body=None, raw=None, headers=None, token=TOKEN):
    h = {"X-GUI-Token": token, **(headers or {})}
    data = raw if raw is not None else json.dumps(body or {}).encode()
    if raw is None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_and_state(base):
    html = urllib.request.urlopen(base + "/").read().decode()
    assert TOKEN in html and "/static/app.js" in html
    assert urllib.request.urlopen(base + "/static/app.js").status == 200
    state = json.loads(urllib.request.urlopen(base + "/api/state").read())
    assert state["catalogue"]["form_factors"] and ".xml" in state["extensions"]


def test_token_required(base):
    status, out = post(base, "/api/evaluate", {}, token="wrong")
    assert status == 403


def test_upload_describe_evaluate_match(base):
    status, out = post(base, "/api/upload", raw=EXAMPLE.read_bytes(), headers={"X-Filename": EXAMPLE.name})
    assert status == 200 and out["added"][0]["n"] == 120
    ds = out["added"][0]["id"]
    status, desc = post(base, "/api/describe", {"model": "sphere", "structure_factor": "hardsphere"})
    assert status == 200
    params = {p["name"]: p["default"] for p in desc["parameters"]}
    status, ev = post(base, "/api/evaluate", {"model": "sphere", "structure_factor": "hardsphere",
                                              "params": params, "dataset": ds, "window": [0.01, None]})
    assert status == 200 and ev["chisq_n"] < 120 and len(ev["residuals"]) == 120
    status, m = post(base, "/api/match", {"model": "sphere", "structure_factor": "hardsphere",
                                         "params": params, "dataset": ds})
    assert status == 200 and m["scale"] > 0
    status, err = post(base, "/api/evaluate", {"model": "nope", "params": {}})
    assert status == 400 and "Unknown" in err["error"]
