"""Regression: SSE stream must deliver a full run to run_complete despite the ~60s ingress response cap.

The server-side fix is `?after_seq=N` replay; the client (frontend api.js streamRun) reconnects on drop.
This test emulates exactly that client behaviour and asserts run_complete is eventually received.
"""
import os
import json
import time

import requests
from dotenv import dotenv_values

env = dotenv_values("/app/frontend/.env")
API = (os.environ.get("REACT_APP_BACKEND_URL") or env["REACT_APP_BACKEND_URL"]).rstrip("/") + "/api"


def test_sse_reconnect_delivers_full_run():
    r = requests.post(f"{API}/runs", json={"url": "https://example.com", "budget": "quick"}, timeout=30)
    assert r.status_code == 200
    run_id = r.json()["id"]
    print("run", run_id)

    seen = {}
    last_seq = 0
    complete = False
    reconnects = 0
    t0 = time.time()

    while time.time() - t0 < 300 and not complete:
        try:
            with requests.get(f"{API}/runs/{run_id}/stream", params={"after_seq": last_seq},
                              stream=True, timeout=(10, 90)) as resp:
                assert resp.status_code == 200
                for line in resp.iter_lines(decode_unicode=True):
                    if line and line.startswith("data: "):
                        try:
                            e = json.loads(line[6:])
                        except Exception:
                            continue
                        if e.get("seq") is not None:
                            assert e["seq"] > last_seq or e["seq"] not in seen, "duplicate replay"
                            last_seq = max(last_seq, e["seq"])
                            seen[e["seq"]] = e
                        if e.get("type") == "run_complete":
                            complete = True
                            break
        except Exception as ex:
            print(f"stream dropped at {round(time.time()-t0,1)}s after seq={last_seq}: {type(ex).__name__}")
        if not complete:
            reconnects += 1
            print(f"reconnect #{reconnects} from seq={last_seq} at {round(time.time()-t0,1)}s")
            time.sleep(1)

    status = requests.get(f"{API}/runs/{run_id}", timeout=30).json()["run"]["status"]
    print("events", len(seen), "reconnects", reconnects, "status", status,
          "elapsed", round(time.time() - t0, 1))
    assert complete, f"never reached run_complete; last_seq={last_seq} status={status}"
    assert status == "completed"
