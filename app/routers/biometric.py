from fastapi import APIRouter, Request, Response, Depends
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import exc as sqlalchemy_exc
import logging
from urllib.parse import parse_qs

from database import SessionLocal
from models import AttendanceLog, AttendanceSession


# ========================= -
# Device Info
# =========================
# Device Name   : S922-W
# Serial Number : AAML174460003
# Vendor        : ZKTeco Inc.
#
# Firmware
# --------
# Firmware Version : 6.5.4 (build 156)
# Algorithm        : ZK Finger VX10.0ii


router = APIRouter(tags=["biometric"])

# Set up logging
logger = logging.getLogger("biometric")

# In-memory buffer for debugging
LAST_ICLOCK: List[Dict[str, Any]] = []
LAST_HANDSHAKES: List[Dict[str, str]] = []
LAST_GETREQUEST_POLLS: List[Dict[str, str]] = []
LAST_PUSH_ACKS: List[Dict[str, str]] = []

# Minimal command queue state (testing)
NEXT_CMD_ID = 9001
PENDING_CLEAR_BY_SN: Dict[str, bool] = {}
WAITING_ACK_BY_SN: Dict[str, int] = {}

# Server-supported Push protocol version (Push 2.32)
SERVER_PUSH_PROTOCOL_VERSION = "2.3.2"
SERVER_TIMEZONE = "2"

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


def _extract_push_ack_fields(text: str) -> Optional[Dict[str, str]]:
    parsed_qs = parse_qs(text, keep_blank_values=True)
    if "ID" not in parsed_qs or "Return" not in parsed_qs:
        return None

    return {
        "id": parsed_qs.get("ID", [""])[0],
        "sn": parsed_qs.get("SN", [""])[0],
        "return": parsed_qs.get("Return", [""])[0],
        "cmd": parsed_qs.get("CMD", [""])[0],
    }


def _next_cmd_id() -> int:
    global NEXT_CMD_ID
    cmd_id = NEXT_CMD_ID
    NEXT_CMD_ID += 1
    return cmd_id


@router.get("/iclock/cdata")
@router.post("/iclock/cdata")
async def iclock_cdata(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    device_sn = request.query_params.get("SN", "unknown")
    table_name = request.query_params.get("table", "unknown")

    # Device polling path
    if request.method == "GET":
        options = request.query_params.get("options", "")
        device_pushver = request.query_params.get("pushver", "")

        if options == "all" or device_pushver:
            LAST_HANDSHAKES.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "sn": device_sn,
                "device_pushver": device_pushver or "(missing)",
                "negotiated": SERVER_PUSH_PROTOCOL_VERSION,
            })
            if len(LAST_HANDSHAKES) > 50:
                LAST_HANDSHAKES.pop(0)
            logger.info(
                f"[iClock] SN={device_sn} pushver={device_pushver or 'none'} PushProtVer={SERVER_PUSH_PROTOCOL_VERSION}"
            )

            handshake_lines = [
                f"GET OPTION FROM: {device_sn}",
                "ErrorDelay=60",
                "Delay=10",
                "TransInterval=1",
                "TransFlag=TransData AttLog",
                f"TimeZone={SERVER_TIMEZONE}",
                "Realtime=1",
                f"PushProtVer={SERVER_PUSH_PROTOCOL_VERSION}",
            ]
            return Response("\n".join(handshake_lines) + "\n", media_type="text/plain")

        return Response("OK\n", media_type="text/plain")

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

    ack = _extract_push_ack_fields(text)
    if ack:
        ack_sn = ack["sn"] or device_sn
        try:
            ack_id = int(ack["id"])
        except ValueError:
            ack_id = -1

        if WAITING_ACK_BY_SN.get(ack_sn) == ack_id:
            WAITING_ACK_BY_SN.pop(ack_sn, None)

        LAST_PUSH_ACKS.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "sn": ack_sn,
            "id": ack["id"],
            "return": ack["return"],
            "cmd": ack["cmd"],
        })
        if len(LAST_PUSH_ACKS) > 50:
            LAST_PUSH_ACKS.pop(0)

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

                # Check if this log entry already exists (device resends old data)
                existing_log = db.query(AttendanceLog).filter(
                    AttendanceLog.pin == pin,
                    AttendanceLog.timestamp == timestamp
                ).first()

                if existing_log:
                    # Already processed this exact log, skip it
                    logger.debug(
                        f"[ATTLOG] Skipping duplicate: pin={pin} dt={timestamp}")
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

                # Pair into attendance sessions (toggle by last open session)
                open_session = db.query(AttendanceSession).filter(
                    AttendanceSession.pin == pin,
                    AttendanceSession.check_out.is_(None),
                ).order_by(AttendanceSession.check_in.desc()).first()

                if open_session:
                    open_session.check_out = timestamp
                    open_session.status = "closed"
                else:
                    session = AttendanceSession(
                        pin=pin,
                        check_in=timestamp,
                        check_out=None,
                        status="open"
                    )
                    db.add(session)
                stored_count += 1

                logger.info(
                    f"[ATTLOG] Stored: pin={pin} dt={timestamp} status={status} "
                    f"verify={verify_type_name}"
                )

            except (ValueError, IndexError) as e:
                logger.error(f"[ATTLOG] Error parsing line '{line}': {e}")
                error_count += 1
                continue
            except Exception as e:
                logger.error(
                    f"[ATTLOG] Unexpected error for line '{line}': {e}")
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

    handshake_rows = []
    for h in reversed(LAST_HANDSHAKES[-20:]):
        handshake_rows.append(
            f"<tr>"
            f"<td>{h['ts']}</td>"
            f"<td>{h['sn']}</td>"
            f"<td>{h['device_pushver']}</td>"
            f"<td>{h['negotiated']}</td>"
            f"</tr>"
        )

    getrequest_rows = []
    for p in reversed(LAST_GETREQUEST_POLLS[-20:]):
        getrequest_rows.append(
            f"<tr>"
            f"<td>{p['ts']}</td>"
            f"<td>{p['sn']}</td>"
            f"</tr>"
        )

    ack_rows = []
    for a in reversed(LAST_PUSH_ACKS[-20:]):
        ack_rows.append(
            f"<tr>"
            f"<td>{a['ts']}</td>"
            f"<td>{a['sn']}</td>"
            f"<td>{a['id']}</td>"
            f"<td>{a['return']}</td>"
            f"<td>{a['cmd']}</td>"
            f"</tr>"
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

        <h2>🤝 Push Handshake Status (last 20)</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp (UTC)</th>
                    <th>SN</th>
                    <th>Device pushver</th>
                    <th>Negotiated PushProtVer</th>
                </tr>
            </thead>
            <tbody>
                {"".join(handshake_rows) if handshake_rows else "<tr><td colspan='4'>No handshake yet (device has not requested options=all/pushver)</td></tr>"}
            </tbody>
        </table>

        <h2>📬 /iclock/getrequest Polls (last 20)</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp (UTC)</th>
                    <th>SN</th>
                </tr>
            </thead>
            <tbody>
                {"".join(getrequest_rows) if getrequest_rows else "<tr><td colspan='2'>No getrequest polls yet</td></tr>"}
            </tbody>
        </table>

        <h2>✅ Push Command ACKs (last 20)</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp (UTC)</th>
                    <th>SN</th>
                    <th>ID</th>
                    <th>Return</th>
                    <th>CMD</th>
                </tr>
            </thead>
            <tbody>
                {"".join(ack_rows) if ack_rows else "<tr><td colspan='5'>No command acknowledgements yet</td></tr>"}
            </tbody>
        </table>

        <h2>🧪 Test: Clear Device ATTLOG</h2>
        <div style="border:1px solid #ccc; padding:10px; margin-bottom:15px; background:#fafafa;">
            <label for="snInput"><b>Device SN:</b></label>
            <input id="snInput" type="text" value="AAML174460003" style="padding:6px; margin:0 8px; width:220px;">
            <button onclick="queueClear()" style="padding:6px 10px; cursor:pointer;">Queue CLEAR LOG</button>
            <div id="clearResult" style="margin-top:8px;"></div>
            <small>This only queues the command. Device receives it on next <code>/iclock/getrequest</code> poll.</small>
        </div>

        <h2>📈 Stats</h2>
        <ul>
            <li>Database logs: {len(recent_logs)}</li>
            <li>In-memory buffer: {len(LAST_ICLOCK)}</li>
            <li>Handshake events: {len(LAST_HANDSHAKES)}</li>
            <li>getrequest polls: {len(LAST_GETREQUEST_POLLS)}</li>
            <li>Push ACK events: {len(LAST_PUSH_ACKS)}</li>
        </ul>

        <script>
            async function queueClear() {{
                const sn = document.getElementById('snInput').value.trim();
                const out = document.getElementById('clearResult');
                if (!sn) {{
                    out.textContent = 'SN is required';
                    return;
                }}
                out.textContent = 'Queuing...';
                try {{
                    const res = await fetch('/admin/device/' + encodeURIComponent(sn) + '/clear-attlog', {{ method: 'POST' }});
                    const data = await res.json();
                    out.textContent = JSON.stringify(data);
                }} catch (err) {{
                    out.textContent = 'Request failed: ' + err;
                }}
            }}
        </script>
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
    LAST_GETREQUEST_POLLS.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "sn": sn,
    })
    if len(LAST_GETREQUEST_POLLS) > 50:
        LAST_GETREQUEST_POLLS.pop(0)

    if sn in WAITING_ACK_BY_SN:
        return Response("OK\n", media_type="text/plain")

    if sn and PENDING_CLEAR_BY_SN.pop(sn, False):
        cmd_id = _next_cmd_id()
        WAITING_ACK_BY_SN[sn] = cmd_id
        payload = f"C:{cmd_id}:CLEAR LOG\n"
        logger.warning(f"[GETREQUEST] SN={sn} -> {payload.strip()}")
        return Response(payload, media_type="text/plain")

    print(f"[GETREQUEST] SN={sn}")
    return Response("OK\n", media_type="text/plain")


@router.post("/iclock/devicecmd")
async def iclock_devicecmd(request: Request):
    raw = await request.body()
    text = raw.decode("utf-8", errors="replace").strip()
    ack = _extract_push_ack_fields(text)

    if ack:
        ack_sn = ack["sn"] or request.query_params.get("SN", "")
        try:
            ack_id = int(ack["id"])
        except ValueError:
            ack_id = -1

        if WAITING_ACK_BY_SN.get(ack_sn) == ack_id:
            WAITING_ACK_BY_SN.pop(ack_sn, None)

        LAST_PUSH_ACKS.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "sn": ack_sn,
            "id": ack["id"],
            "return": ack["return"],
            "cmd": ack["cmd"],
        })
        if len(LAST_PUSH_ACKS) > 50:
            LAST_PUSH_ACKS.pop(0)

    LAST_ICLOCK.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "client": str(request.client),
        "method": request.method,
        "query": dict(request.query_params),
        "body": text,
    })
    if len(LAST_ICLOCK) > 50:
        LAST_ICLOCK.pop(0)

    return Response("OK\n", media_type="text/plain")


@router.post("/admin/device/{sn}/clear-attlog")
async def admin_clear_attlog(sn: str):
    PENDING_CLEAR_BY_SN[sn] = True
    logger.warning(f"[ADMIN] Queued CLEAR LOG for SN={sn}")
    return {"ok": True, "sn": sn, "queued": "CLEAR LOG"}
