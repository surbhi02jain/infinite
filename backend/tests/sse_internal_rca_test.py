"""RCA: compare SSE lifetime via internal port vs public ingress."""
import json
import time
import requests

INTERNAL = "http://localhost:8001/api"


def test_internal_sse_lifetime():
    run_id = requests.post(f"{INTERNAL}/runs", json={"url": "https://example.com"}, timeout=30).json()["id"]
    t0 = time.time()
    complete = False
    n = 0
    with requests.get(f"{INTERNAL}/runs/{run_id}/stream", stream=True, timeout=(10, 240)) as resp:
        for line in resp.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                n += 1
                e = json.loads(line[6:])
                if e.get("type") == "run_complete":
                    complete = True
                    break
    print(f"INTERNAL closed_at={round(time.time()-t0,1)}s events={n} complete={complete}")
    assert complete, "internal SSE also dropped early"
