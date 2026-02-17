from fastapi import APIRouter, Request, Response, Depends
from fastapi.responses import JSONResponse
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import exc as sqlalchemy_exc
import logging

from database import SessionLocal
from models import AttendanceLog, AttendanceSession


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

    # Check if this is a GET request (device polling) — if so, push employees
    if request.method == "GET":
        from models import Employee
        employees = db.query(Employee).all()
        if employees:
            commands = []
            for emp in employees:
                # Format: C:USER ADD PIN=id Name=name Privilege=0
                # Replace spaces with underscores in name/surname for device compatibility
                full_name = f"{emp.Name_}_{emp.Surname_}".replace(" ", "_")
                cmd = f"C:USER ADD PIN={emp.Employee_id} Name={full_name} Privilege=0"
                commands.append(cmd)

            response_text = "\n".join(commands) + "\n"
            logger.info(
                f"[iClock] Pushing {len(employees)} employees to device {device_sn}")
            return Response(response_text, media_type="text/plain")
        else:
            # No employees, return OK
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

    # ---- ATTLOG parsing (attendance events) ----
    if request.method == "POST" and table_name == "ATTLOG":
        lines = text.splitlines()
        stored_count = 0
        error_count = 0
        logger.info(f"[ATTLOG] Processing {len(lines)} lines from device {device_sn}")

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
                logger.debug(f"[ATTLOG] Added log for pin={pin}")

                # Pair into attendance sessions (toggle by last open session)
                try:
                    open_session = db.query(AttendanceSession).filter(
                        AttendanceSession.pin == pin,
                        AttendanceSession.check_out.is_(None),
                    ).order_by(AttendanceSession.check_in.desc()).first()

                    if open_session:
                        open_session.check_out = timestamp
                        open_session.status = "closed"
                        logger.debug(f"[ATTLOG] Closed session for pin={pin}")
                    else:
                        session = AttendanceSession(
                            pin=pin,
                            check_in=timestamp,
                            check_out=None,
                            status="open"
                        )
                        db.add(session)
                        logger.debug(f"[ATTLOG] Created new session for pin={pin}")
                except Exception as query_error:
                    logger.error(f"[ATTLOG] Error querying/updating session for pin={pin}: {query_error}")
                    raise
                
                stored_count += 1

                logger.info(
                    f"[ATTLOG] Processed: pin={pin} dt={timestamp} status={status} "
                    f"verify={verify_type_name}"
                )

            except (ValueError, IndexError) as e:
                logger.error(f"[ATTLOG] Error parsing line '{line}': {e}")
                error_count += 1
                continue
            except Exception as e:
                logger.error(
                    f"[ATTLOG] Unexpected error for line '{line}': {e}", exc_info=True)
                error_count += 1
                continue

        # Commit all records at once
        logger.info(f"[ATTLOG] Attempting to commit {stored_count} records...")
        try:
            db.commit()
            logger.info(
                f"[ATTLOG] ✓ Commit successful: {stored_count} stored, {error_count} errors")
        except sqlalchemy_exc.SQLAlchemyError as e:
            logger.error(f"[ATTLOG] ✗ DATABASE COMMIT FAILED: {e}", exc_info=True)
            db.rollback()
            return Response("ERROR\n", media_type="text/plain", status_code=500)
        except Exception as e:
            logger.error(f"[ATTLOG] ✗ UNEXPECTED ERROR DURING COMMIT: {e}", exc_info=True)
            db.rollback()
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
        table_name = e.get('query', {}).get('table', 'UNKNOWN')
        raw_rows.append(
            f"<pre style='font-size: 11px; margin: 5px 0;'>"
            f"<strong>Table: {table_name}</strong> | {e['ts']} | {e['client']} | {e['method']}<br>"
            f"query={e['query']}<br>"
            f"body={e['body'][:200]}"
            f"</pre><hr style='margin: 3px 0;'>"
        )

    html = f"""
    <html>
    <head>
        <title>iClock Debug</title>
        <style>
            body {{ font-family: monospace; margin: 20px; background: #f5f5f5; }}
            h2 {{ border-bottom: 2px solid #333; padding-bottom: 10px; margin-top: 30px; }}
            table {{ border-collapse: collapse; width: 100%; background: white; }}
            th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
            th {{ background: #3498db; color: white; }}
            tr:nth-child(even) {{ background: #f9f9f9; }}
            .stat-box {{ background: white; padding: 15px; margin: 10px 0; border-left: 4px solid #3498db; }}
            .success {{ color: #27ae60; font-weight: bold; }}
            .warning {{ color: #e67e22; font-weight: bold; }}
            .error {{ color: #e74c3c; font-weight: bold; }}
        </style>
    </head>
    <body>
        <h1>🔍 iClock Biometric Debug Panel</h1>
        
        <div class="stat-box">
            <h3>📈 Statistics</h3>
            <p><span class="{'success' if len(recent_logs) > 0 else 'warning'}">Database logs: {len(recent_logs)}</span></p>
            <p>In-memory buffer: {len(LAST_ICLOCK)}</p>
        </div>

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
                {"".join(db_rows) if db_rows else "<tr><td colspan='6' style='text-align:center; color: #e74c3c;'>❌ No logs in database yet</td></tr>"}
            </tbody>
        </table>

        <h2>📡 Raw iClock Hits (last 20, in-memory)</h2>
        <p><em>Use TABLE NAME to verify ATTLOG requests are coming in:</em></p>
        {"".join(raw_rows) if raw_rows else "<p style='color: #e74c3c;'>❌ No raw hits received yet</p>"}

        <h2>🧪 Database Connection Test</h2>
        <p><a href="/biometric/test-db" style="background: #3498db; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px;">
            Test Database Write
        </a></p>
    </body>
    </html>
    """

    return Response(html, media_type="text/html")


@router.get("/biometric/test-db")
async def test_database_write(db: Session = Depends(get_db)):
    """
    Test endpoint to verify database is working correctly
    """
    try:
        test_log = AttendanceLog(
            pin="TEST-PIN-001",
            timestamp=datetime.now(),
            status=0,
            verify_type=0,
            verify_type_name="test",
            raw_data="TEST RECORD",
            device_sn="TEST-DEVICE"
        )
        db.add(test_log)
        db.commit()
        logger.info("✓ Test record successfully written to database")
        return JSONResponse({
            "status": "success",
            "message": "✓ Database connection is working! Test record written successfully.",
            "test_record": {
                "pin": test_log.pin,
                "timestamp": test_log.timestamp.isoformat(),
            }
        })
    except Exception as e:
        logger.error(f"✗ Database test failed: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": f"✗ Database connection FAILED: {e}",
            "error_type": type(e).__name__
        }, status_code=500)



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
