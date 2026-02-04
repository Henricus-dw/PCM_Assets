from fastapi import APIRouter, Request, Response
from datetime import datetime, timezone
import os

router = APIRouter(tags=["biometric"])


LOG_PATH = "biometric_raw.log"


@router.post("/punch")
async def biometric_punch(request: Request):
    """
    Temporary endpoint: capture ANY incoming payload from the device.
    No parsing. No DB writes. Just logging for debugging.
    """
    raw_bytes = await request.body()
    headers = dict(request.headers)

    stamp = datetime.now(timezone.utc).isoformat()

    # Append to a log file (safe for first testing)
    with open(LOG_PATH, "ab") as f:
        f.write(f"\n--- {stamp} UTC ---\n".encode("utf-8"))
        f.write(f"Client: {request.client}\n".encode("utf-8"))
        f.write(f"Headers: {headers}\n".encode("utf-8"))
        f.write(b"Body:\n")
        f.write(raw_bytes[:10000])  # cap to avoid huge spam
        f.write(b"\n")

    # Also print a short line to your console logs
    print(
        f"[BIOMETRIC] {stamp} received {len(raw_bytes)} bytes from {request.client}")

    return {"ok": True}


@router.post("/iclock/cdata")
async def biometric_adms(request: Request):
    raw_bytes = await request.body()
    headers = dict(request.headers)

    stamp = datetime.now(timezone.utc).isoformat()

    with open(LOG_PATH, "ab") as f:
        f.write(f"\n--- {stamp} UTC ---\n".encode("utf-8"))
        f.write(f"Client: {request.client}\n".encode("utf-8"))
        f.write(f"Headers: {headers}\n".encode("utf-8"))
        f.write(b"Body:\n")
        f.write(raw_bytes[:10000])
        f.write(b"\n")

    print(
        f"[BIOMETRIC-ADMS] {stamp} received {len(raw_bytes)} bytes from {request.client}")

    return {"ok": True}


@router.get("/iclock/cdata")
async def iclock_cdata(request: Request):
    params = dict(request.query_params)
    stamp = datetime.now(timezone.utc).isoformat()

    with open("/var/www/pcm_tracker/biometric_raw.log", "ab") as f:
        f.write(f"\n--- {stamp} UTC ---\n".encode())
        f.write(f"Client: {request.client}\n".encode())
        f.write(f"Params: {params}\n".encode())

    print(f"[ICLOCK] handshake from {request.client} params={params}")

    # REQUIRED by iClock protocol
    return "OK"


@router.get("/iclock/cdata")
@router.post("/iclock/cdata")
async def iclock_cdata(request: Request):
    raw = await request.body()
    print("ICLOCK DATA RECEIVED:", raw[:500])
    return Response("OK")
