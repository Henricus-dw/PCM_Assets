from fastapi import APIRouter, Request, Response
from datetime import datetime, timezone
from typing import List, Dict, Any

router = APIRouter(tags=["biometric"])

# In-memory buffer for debugging
LAST_ICLOCK: List[Dict[str, Any]] = []


@router.get("/iclock/cdata")
@router.post("/iclock/cdata")
async def iclock_cdata(request: Request):
    raw = await request.body()

    # Always record the hit for debugging
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "client": str(request.client),
        "method": request.method,
        "query": dict(request.query_params),
        "body": raw.decode("utf-8", errors="replace"),
    }

    LAST_ICLOCK.append(entry)
    if len(LAST_ICLOCK) > 50:
        LAST_ICLOCK.pop(0)

    # --- ATTENDANCE LOG PARSING ---
    text = raw.decode("utf-8", errors="replace").strip()

    if (
        request.method == "POST"
        and request.query_params.get("table") == "ATTLOG"
        and text
    ):
        for line in text.splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue

            pin = parts[0].strip()
            dt_str = parts[1].strip()
            status = parts[2].strip()
            verify = parts[3].strip()

            print(
                f"[ATTLOG] pin={pin} dt={dt_str} status={status} verify={verify}"
            )

    # IMPORTANT: newline is required for iClock devices
    return Response("OK\n", media_type="text/plain")


@router.get("/biometric/debug")
async def biometric_debug():
    rows = []
    for e in reversed(LAST_ICLOCK[-20:]):
        rows.append(
            f"<pre>{e['ts']} | {e['client']} | {e['method']}\n"
            f"query={e['query']}\n"
            f"body={e['body']}\n</pre><hr>"
        )

    html = (
        "<h2>Last iClock hits</h2>" + "".join(rows)
        if rows
        else "<h2>No hits yet</h2>"
    )

    return Response(html, media_type="text/html")
