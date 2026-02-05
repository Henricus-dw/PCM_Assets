from fastapi import APIRouter, Request, Response
from datetime import datetime, timezone
from typing import List, Dict, Any
import os

LAST_ICLOCK: List[Dict[str, Any]] = []

router = APIRouter(tags=["biometric"])


LOG_PATH = "biometric_raw.log"


@router.get("/iclock/cdata")
@router.post("/iclock/cdata")
async def iclock_cdata(request: Request):
    raw = await request.body()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "client": str(request.client),
        "method": request.method,
        "query": dict(request.query_params),
        "body": raw[:2000].decode("utf-8", errors="replace"),
    }

    LAST_ICLOCK.append(entry)
    if len(LAST_ICLOCK) > 50:
        LAST_ICLOCK.pop(0)

    return Response("OK\n", media_type="text/plain")


@router.get("/biometric/debug")
async def biometric_debug():
    # Minimal HTML page so you can see it in a browser without server access
    rows = []
    for e in reversed(LAST_ICLOCK[-20:]):
        rows.append(
            f"<pre>{e['ts']} | {e['client']} | {e['method']}\n"
            f"query={e['query']}\n"
            f"body={e['body']}\n</pre><hr>"
        )
    html = "<h2>Last iClock hits</h2>" + \
        "".join(rows) if rows else "<h2>No hits yet</h2>"
    return Response(html, media_type="text/html")


text = raw.decode("utf-8", errors="replace").strip()

# Only process uploads
if request.method == "POST" and request.query_params.get("table") == "ATTLOG" and text:
    # Each row: PIN \t YYYY-MM-DD HH:MM:SS \t Status \t Verify
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue

        pin = parts[0].strip()
        dt_str = parts[1].strip()
        status = parts[2].strip()
        verify = parts[3].strip()

        # TODO: insert into your DB here (we'll map PIN -> employee next)
        print(
            f"[ATTLOG] pin={pin} dt={dt_str} status={status} verify={verify}")
