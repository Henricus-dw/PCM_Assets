from fastapi import APIRouter, Request, Response, Depends
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import exc as sqlalchemy_exc
import logging

from database import SessionLocal
from models import AttendanceLog


# =========================
# Device Info
# =========================
# Device Name   : S922-W
# Serial Number : AAML174460003
# Vendor        : ZKTeco Inc.
#
# Firmware
# --------
# Firmware Version : 6.5.4 (build 156)
# Algorithm        : ZK Finger VX10.0


router = APIRouter(tags=["biometric"])

# Set up logging
logger = logging.getLogger("biometric")

# In-memory buffer for debugging
LAST_ICLOCK: List[Dict[str, Any]] = []

# Map verify_type codes to human-readable names
VERIFY_TYPE_MAP = {
    0: "fingerprint",
    1: "password",
    2: "rfid_card",
    3: "face",
    4: "palm",
    255: "unknown"
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_iclock_datetime(dt_str: str) -> Optional[datetime]:
    """
    Parse iClock datetime format: "YYYY-MM-DD HH:MM:SS"
    Returns datetime in UTC, or None if parsing fails.
    """
    if not dt_str:
        return None
    try:
        # iClock uses local device time, we treat as UTC for now
        dt = datetime.strptime(dt_str.strip(), "%Y-%m-%d %H:%M:%S")
        return dt
    except ValueError as e:
        logger.warning(f"Failed to parse datetime '{dt_str}': {e}")
        return None


@router.get("/iclock/cdata")
@router.post("/iclock/cdata")
async def iclock_cdata(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    device_sn = request.query_params.get("SN", "unknown")
    table_name = request.query_params.get("table", "unknown")

    # Always store the raw hit for debugging
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

    text = raw.decode("utf-8", errors="replace").strip()
    logger.info(
        f"[iClock] SN={device_sn} table={table_name} method={request.method}")

    # ---- ATTLOG parsing (attendance events) ----
    if request.method == "POST" and table_name == "ATTLOG":
        lines = text.splitlines()
        stored_count = 0
        error_count = 0

        for line in lines:
            if not line.strip():
                continue

            parts = line.split("\t")
            if len(parts) < 4:
                logger.warning(
                    f"[ATTLOG] Skipping malformed line (< 4 fields): {line}")
                error_count += 1
                continue

            try:
                pin = parts[0].strip()
                dt_str = parts[1].strip()
                status = int(parts[2].strip())
                verify_type = int(parts[3].strip())

                # Parse datetime
                timestamp = parse_iclock_datetime(dt_str)
                if not timestamp:
                    logger.warning(
                        f"[ATTLOG] Skipping line with invalid datetime: {line}")
                    error_count += 1
                    continue

                # Look up human-readable verify type name
                verify_type_name = VERIFY_TYPE_MAP.get(verify_type, "unknown")

                # Create and store attendance record
                log = AttendanceLog(
                    pin=pin,
                    timestamp=timestamp,
                    status=status,
                    verify_type=verify_type,
                    verify_type_name=verify_type_name,
                    raw_data=line,
                    device_sn=device_sn
                )

                db.add(log)
                stored_count += 1

                logger.info(
                    f"[ATTLOG] Stored: pin={pin} dt={timestamp} status={status} "
                    f"verify={verify_type_name}"
                )

            except (ValueError, IndexError) as e:
                logger.error(f"[ATTLOG] Error parsing line '{line}': {e}")
                error_count += 1
                continue

        # Commit all records at once
        try:
            db.commit()
            logger.info(
                f"[ATTLOG] Commit successful: {stored_count} stored, {error_count} errors")
        except sqlalchemy_exc.SQLAlchemyError as e:
            db.rollback()
            logger.error(f"[ATTLOG] Database commit failed: {e}")
            return Response("ERROR\n", media_type="text/plain", status_code=500)

    # REQUIRED for iClock devices - always return OK
    return Response("OK\n", media_type="text/plain")


@router.get("/biometric/debug")
async def biometric_debug(db: Session = Depends(get_db)):
    """
    Debug endpoint showing:
    1. Last 20 raw iClock hits
    2. Last 20 parsed attendance logs from database
    """
    # Get last 20 attendance logs from database
    recent_logs = db.query(AttendanceLog).order_by(
        AttendanceLog.received_at.desc()
    ).limit(20).all()

    db_rows = []
    for log in recent_logs:
        db_rows.append(
            f"<tr>"
            f"<td>{log.timestamp}</td>"
            f"<td>{log.pin}</td>"
            f"<td>{log.status}</td>"
            f"<td>{log.verify_type_name}</td>"
            f"<td>{log.device_sn}</td>"
            f"<td><small>{log.received_at.isoformat()}</small></td>"
            f"</tr>"
        )

    # Get last 20 raw hits from in-memory buffer
    raw_rows = []
    for e in reversed(LAST_ICLOCK[-20:]):
        raw_rows.append(
            f"<pre style='font-size: 11px; margin: 5px 0;'>"
            f"{e['ts']} | {e['client']} | {e['method']}<br>"
            f"query={e['query']}<br>"
            f"body={e['body'][:200]}"
            f"</pre><hr style='margin: 3px 0;'>"
        )

    html = f"""
    <html>
    <head>
        <title>iClock Debug</title>
        <style>
            body {{ font-family: monospace; margin: 20px; }}
            h2 {{ border-bottom: 2px solid #333; padding-bottom: 10px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
            th {{ background: #f0f0f0; }}
            tr:nth-child(even) {{ background: #f9f9f9; }}
        </style>
    </head>
    <body>
        <h1>iClock Biometric Debug Panel</h1>
        
        <h2>📊 Parsed Attendance Logs (from database)</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp</th>
                    <th>PIN</th>
                    <th>Status</th>
                    <th>Verify Type</th>
                    <th>Device SN</th>
                    <th>Received At</th>
                </tr>
            </thead>
            <tbody>
                {"".join(db_rows) if db_rows else "<tr><td colspan='6'>No logs yet</td></tr>"}
            </tbody>
        </table>

        <h2>📡 Raw iClock Hits (last 20, in-memory)</h2>
        {"".join(raw_rows) if raw_rows else "<p>No hits yet</p>"}

        <h2>📈 Stats</h2>
        <ul>
            <li>Database logs: {len(recent_logs)}</li>
            <li>In-memory buffer: {len(LAST_ICLOCK)}</li>
        </ul>
    </body>
    </html>
    """

    return Response(html, media_type="text/html")


@router.get("/biometric/logs")
async def get_attendance_logs(
    db: Session = Depends(get_db),
    pin: Optional[str] = None,
    limit: int = 50
):
    """
    JSON endpoint to retrieve attendance logs.
    Query params:
    - pin: Filter by employee PIN
    - limit: Max results (default 50, max 500)
    """
    limit = min(limit, 500)
    query = db.query(AttendanceLog).order_by(AttendanceLog.timestamp.desc())

    if pin:
        query = query.filter(AttendanceLog.pin == pin)

    logs = query.limit(limit).all()

    return [
        {
            "id": log.id,
            "pin": log.pin,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "status": log.status,
            "verify_type": log.verify_type,
            "verify_type_name": log.verify_type_name,
            "device_sn": log.device_sn,
            "received_at": log.received_at.isoformat() if log.received_at else None,
        }
        for log in logs
    ]


@router.get("/iclock/getrequest")
async def iclock_getrequest(request: Request):
    sn = request.query_params.get("SN", "")
    # For now: no commands, just acknowledge correctly
    print(f"[GETREQUEST] SN={sn}")
    return Response("OK\n", media_type="text/plain")
