from fastapi import Request
from app.routers.biometric import router as biometric_router
from fastapi import Body
from sqlalchemy import desc
from datetime import datetime
from sqlalchemy import text, Column, Integer, String, Float, DateTime, func, or_, exc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session
from fastapi import FastAPI, Request, Form, Depends, HTTPException, APIRouter, Path
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime, timedelta
import calendar
import os
from starlette.responses import RedirectResponse
import json
import requests

from auth import get_current_user, require_admin

# ---- Your models & DB ----
from models import VodacomSubscription, Device, User, PendingUser, DeviceEditRequest, ContractEditRequest
from database import SessionLocal, engine, Base


# Create all tables (only needed once)
Base.metadata.create_all(bind=engine)
router = APIRouter()
# ---- App setup ----
app = FastAPI()
app.include_router(router)
# IMPORTANT for local dev: https_only=False so the browser will send the cookie over http://127.0.0.1
app.add_middleware(
    SessionMiddleware,
    # set a strong value in prod
    secret_key=os.environ.get("SECRET_KEY", "dev-change-me"),
    same_site="lax",
    https_only=False  # True only in production with HTTPS
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


app.include_router(biometric_router)

# Static files & templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.auto_reload = True
templates.env.cache = {}
templates.env.cache_size = 0

# DB dependency


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Disable caching for /static/* (handy for dev)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# -------------- AUTH PAGES --------------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password."},
            status_code=400,
        )
    request.session["user_id"] = user.id
    request.session["is_admin"] = bool(getattr(user, "is_admin", False))
    return RedirectResponse(url="/", status_code=302)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)

# -------------- PAGE ROUTES (ALL GUARDED) --------------
# 1) LANDING PAGE "/" -> module selection


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    db = SessionLocal()
    try:
        current_user = db.get(User, request.session.get("user_id"))
        return templates.TemplateResponse("landing.html", {"request": request, "current_user": current_user})
    finally:
        db.close()


# 2) TIME & ATTENDANCE + BIOMETRIC


@app.get("/time-attendance", response_class=HTMLResponse)
def time_attendance_home(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "employees_html.html",
        {"request": request, "section": "time-attendance",
            "time": datetime.utcnow().timestamp()}
    )


@app.get("/api/employees")
def api_list_employees(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from models import Employee
    rows = db.query(Employee).all()
    out = []
    for r in rows:
        out.append({
            "PIN": getattr(r, 'PIN'),
            "Employee_id": getattr(r, 'Employee_id'),
            "Name_": getattr(r, 'Name_'),
            "Surname_": getattr(r, 'Surname_'),
            "Company": getattr(r, 'Company'),
            "Department": getattr(r, 'Department'),
        })
    return JSONResponse(out)


@app.get("/api/employees/summary")
def api_employees_summary(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from models import Employee, AttendanceLog, AttendanceSession

    employees = db.query(Employee).all()

    today = date.today()
    start = datetime.combine(today, datetime.min.time())
    end = datetime.combine(today, datetime.max.time())
    logs_today = db.query(AttendanceLog).filter(
        AttendanceLog.timestamp >= start,
        AttendanceLog.timestamp <= end
    ).order_by(AttendanceLog.timestamp.desc()).all()

    # Latest event per pin (string form)
    recent_logs = db.query(AttendanceLog).order_by(
        AttendanceLog.timestamp.desc()).limit(2000).all()
    last_event = {}
    for log in recent_logs:
        if log.pin not in last_event:
            last_event[log.pin] = log

    # Latest session per pin
    recent_sessions = db.query(AttendanceSession).order_by(
        AttendanceSession.check_in.desc()).limit(2000).all()
    last_session = {}
    for s in recent_sessions:
        if s.pin not in last_session:
            last_session[s.pin] = s

    open_sessions = db.query(AttendanceSession).filter(
        AttendanceSession.check_out.is_(None)).all()
    open_by_pin = {s.pin for s in open_sessions}

    active_today_pins = {log.pin for log in logs_today}
    late_cutoff = datetime.combine(
        today, datetime.min.time()) + timedelta(hours=9)
    late_arrivals = {log.pin for log in logs_today if log.status ==
                     0 and log.timestamp > late_cutoff}

    rows = []
    for emp in employees:
        pin_str = str(emp.PIN)
        le = last_event.get(pin_str)
        ls = last_session.get(pin_str)
        rows.append({
            "PIN": emp.PIN,
            "Employee_id": emp.Employee_id,
            "Name_": emp.Name_,
            "Surname_": emp.Surname_,
            "Company": emp.Company,
            "Department": emp.Department,
            "last_event": le.timestamp.isoformat() if le else None,
            "last_status": le.status if le else None,
            "last_check_in": ls.check_in.isoformat() if ls else None,
            "current_status": "IN" if pin_str in open_by_pin else "OUT",
        })

    return JSONResponse({
        "totals": {
            "employees": len(employees),
            "active_today": len(active_today_pins),
            "open_sessions": len(open_by_pin),
            "late_arrivals": len(late_arrivals),
        },
        "rows": rows,
    })


@app.get("/api/employees/{pin}/events")
def api_employee_events(pin: int, request: Request, db: Session = Depends(get_db), limit: int = 20):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from models import AttendanceLog
    pin_str = str(pin)
    logs = db.query(AttendanceLog).filter(AttendanceLog.pin == pin_str).order_by(
        AttendanceLog.timestamp.desc()).limit(limit).all()
    return JSONResponse([
        {
            "timestamp": l.timestamp.isoformat() if l.timestamp else None,
            "status": l.status,
        }
        for l in logs
    ])


@app.get("/api/employees/{pin}/session")
def api_employee_session(pin: int, request: Request, db: Session = Depends(get_db), date_str: Optional[str] = None):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from models import AttendanceSession
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date")
    else:
        target_date = date.today()

    pin_str = str(pin)
    sessions = db.query(AttendanceSession).filter(
        AttendanceSession.pin == pin_str,
        func.date(AttendanceSession.check_in) == target_date.isoformat()
    ).order_by(AttendanceSession.check_in.desc()).all()

    def duration_minutes(s):
        if s.check_out and s.check_in:
            return int((s.check_out - s.check_in).total_seconds() / 60)
        return None

    return JSONResponse([
        {
            "check_in": s.check_in.isoformat() if s.check_in else None,
            "check_out": s.check_out.isoformat() if s.check_out else None,
            "status": s.status,
            "duration_minutes": duration_minutes(s),
        }
        for s in sessions
    ])


@app.get("/api/attendance/live")
def api_attendance_live(request: Request, db: Session = Depends(get_db), limit: int = 50):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from models import AttendanceLog
    logs = db.query(AttendanceLog).order_by(
        AttendanceLog.timestamp.desc()).limit(limit).all()

    def status_to_action(status: Optional[int]) -> str:
        if status == 0:
            return "check_in"
        if status == 1:
            return "check_out"
        return "unknown"

    return JSONResponse([
        {
            "pin": l.pin,
            "timestamp": l.timestamp.isoformat() if l.timestamp else None,
            "status": l.status,
            "action": status_to_action(l.status),
        }
        for l in logs
    ])


@app.get("/api/sessions/today")
def api_sessions_today(request: Request, db: Session = Depends(get_db), date_str: Optional[str] = None, pin: Optional[str] = None):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from models import AttendanceSession
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date")
    else:
        target_date = date.today()

    query = db.query(AttendanceSession).filter(
        func.date(AttendanceSession.check_in) == target_date.isoformat())
    if pin:
        query = query.filter(AttendanceSession.pin == pin)
    sessions = query.order_by(AttendanceSession.check_in.desc()).all()

    def duration_minutes(s):
        if s.check_out and s.check_in:
            return int((s.check_out - s.check_in).total_seconds() / 60)
        return None

    return JSONResponse([
        {
            "pin": s.pin,
            "check_in": s.check_in.isoformat() if s.check_in else None,
            "check_out": s.check_out.isoformat() if s.check_out else None,
            "status": s.status,
            "duration_minutes": duration_minutes(s),
        }
        for s in sessions
    ])


@app.post("/api/employees")
async def api_create_employee(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = await request.json()
    Employee_id = payload.get('Employee_id')
    if not Employee_id:
        raise HTTPException(status_code=400, detail="Employee_id required")
    from models import Employee
    # Prevent accidental overwrite
    existing = db.query(Employee).filter(
        Employee.Employee_id == Employee_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Employee already exists")
    emp = Employee(
        Employee_id=Employee_id,
        Name_=payload.get('Name_'),
        Surname_=payload.get('Surname_'),
        Company=payload.get('Company'),
        Department=payload.get('Department')
    )
    db.add(emp)
    try:
        db.commit()
        db.refresh(emp)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse({
        "status": "ok",
        "PIN": emp.PIN,
        "Employee_id": emp.Employee_id,
    })


@app.delete("/api.employees/{employee_id}")
def api_delete_employee(employee_id: str, request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from models import Employee
    row = db.query(Employee).filter(
        Employee.Employee_id == employee_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()
    return JSONResponse({"status": "ok"})


@app.post("/api/devices/push_employees")
async def api_push_employees(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = await request.json()
    device_url = payload.get("device_url")
    if not device_url:
        raise HTTPException(status_code=400, detail="device_url required")

    from models import Employee
    rows = db.query(Employee).all()
    data = []
    for r in rows:
        data.append({
            "Employee_id": getattr(r, "Employee_id"),
            "Name_": getattr(r, "Name_"),
            "Surname_": getattr(r, "Surname_"),
            "Company": getattr(r, "Company"),
            "Department": getattr(r, "Department"),
        })

    try:
        resp = requests.post(device_url, json={"employees": data}, timeout=15)
        return JSONResponse({"status": "ok", "device_status_code": resp.status_code, "device_response": resp.text})
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to reach device: {e}")


@app.get("/biometric-dashboard", response_class=HTMLResponse)
def biometric_dashboard(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "biometric_dashboard.html",
        {"request": request, "section": "biometric-dashboard",
            "time": datetime.utcnow().timestamp()}
    )


# 3) VODACOM HOME DASHBOARD
def dashboard_home(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard_home.html",
        {"request": request, "section": "home"}
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_home_alias(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard_home.html",
        {"request": request, "section": "home"}
    )


@app.get("/dashboard/home", response_class=HTMLResponse)
def dashboard_home_explicit(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard_home.html",
        {"request": request, "section": "home"}
    )

# 4) DASHBOARD VODACOM


@app.get("/dashboard/vodacom", response_class=HTMLResponse)
def dashboard_vodacom(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    db: Session = SessionLocal()
    try:
        # Get all subscriptions
        records = db.query(VodacomSubscription).order_by(
            VodacomSubscription.id.desc()).all()
        # Attach devices to each subscription
        for record in records:
            record.devices = db.query(Device).filter(
                Device.vd_id == record.id).all()
    finally:
        db.close()

    return templates.TemplateResponse(
        "dashboard_vodacom.html",
        {"request": request, "records": records, "section": "vodacom"}
    )

# 5) DASHBOARD DEVICES


@app.get("/dashboard/devices", response_class=HTMLResponse)
def dashboard_devices(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    # 1) Load devices
    devices = db.query(Device).order_by(Device.id.desc()).all()

    # 2) Build device_id -> list of owner lines
    owners_map = {}
    if devices:
        device_ids = [d.id for d in devices]
        params = {f"id{i}": did for i, did in enumerate(device_ids)}
        placeholders = ",".join(f":id{i}" for i in range(len(device_ids)))

        rows = db.execute(
            text(f"""
                SELECT d_id, Name_, Surname_, Personnel_nr, Company
                FROM Past_device_owners
                WHERE d_id IN ({placeholders})
                ORDER BY d_id
            """),
            params
        ).fetchall()

        for d_id, Name_, Surname_, Personnel_nr, Company in rows:
            line = f"{Name_} {Surname_} ({Company})"
            owners_map.setdefault(d_id, []).append(line)

    # 3) Attach a newline-joined display string to each device
    for d in devices:
        d.past_owners_display = "\n".join(owners_map.get(d.id, []))

    return templates.TemplateResponse(
        "dashboard_devices.html",
        {"request": request, "devices": devices, "section": "devices"}
    )


@app.get("/form", response_class=HTMLResponse)
def vodacom_form(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "form.html",
        {"request": request, "section": "form",
            "time": datetime.utcnow().timestamp()}
    )

# -------------- FORM HANDLERS (OPTIONALLY GUARDED) --------------


@app.post("/submit", response_class=HTMLResponse)
def submit_form(
    request: Request,
    Name_: str = Form(...),
    Surname_: str = Form(...),
    Personnel_nr: str = Form(...),
    Company: str = Form(...),
    Client_Division: str = Form(...),
    Contract_Type: str = Form(...),
    Monthly_Costs: float = Form(...),
    VAT: float = Form(...),
    Monthly_Cost_Excl_VAT: float = Form(...),
    Contract_Term: str = Form(...),
    Inception_Date: str = Form(...),
    Termination_Date: str = Form(...),
    Sim_Card_Number: str = Form(...),
    db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    subscription = VodacomSubscription(
        Name_=Name_,
        Surname_=Surname_,
        Personnel_nr=Personnel_nr,
        Company=Company,
        Client_Division=Client_Division,
        Contract_Type=Contract_Type,
        Monthly_Costs=Monthly_Costs,
        VAT=VAT,
        Monthly_Cost_Excl_VAT=Monthly_Cost_Excl_VAT,
        Contract_Term=Contract_Term,
        Sim_Card_Number=Sim_Card_Number,
        Inception_Date=datetime.strptime(Inception_Date, '%Y-%m-%d'),
        Termination_Date=datetime.strptime(Termination_Date, '%Y-%m-%d'),
    )
    db.add(subscription)
    db.commit()
    return templates.TemplateResponse("form.html", {"request": request, "message": "Form submitted successfully!", "section": "form"})


@app.post("/submit_device", response_class=HTMLResponse)
def submit_device(
    request: Request,
    AName_: str = Form(...),
    ASurname_: str = Form(...),
    ACompany: str = Form(...),
    AClient_Division: str = Form(...),
    Device_Name: str = Form(...),
    Serial_Number: str = Form(...),
    APersonnel_nr=Form(...),
    Device_Description: str = Form(...),
    insurance: str = Form(...),
    db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    device = Device(
        Name_=AName_,
        Surname_=ASurname_,
        Personnel_nr=APersonnel_nr,
        Company=ACompany,
        Client_Division=AClient_Division,
        Device_Name=Device_Name,
        Serial_Number=Serial_Number,
        Device_Description=Device_Description,
        insurance=insurance
    )
    db.add(device)
    db.commit()
    return templates.TemplateResponse("form.html", {"request": request, "message": "Device saved successfully!", "section": "form"})


@app.post("/submit_all", response_class=RedirectResponse)
def submit_all_forms(
    request: Request,
    # Vodacom Subscription fields
    Name_: str = Form(...),
    Surname_: str = Form(...),
    Personnel_nr: str = Form(...),
    Company: str = Form(...),
    Client_Division: str = Form(...),
    Contract_Type: str = Form(...),
    Monthly_Costs: float = Form(...),
    VAT: float = Form(...),
    Monthly_Cost_Excl_VAT: float = Form(...),
    Contract_Term: str = Form(...),
    Inception_Date: str = Form(...),
    Termination_Date: str = Form(...),
    Sim_Card_Number: str = Form(...),

    # Device 1 fields (required)
    AName_1: str = Form(...),
    ASurname_1: str = Form(...),
    APersonnel_nr_1: str = Form(...),
    ACompany_1: str = Form(...),
    AClient_Division_1: str = Form(...),
    Device_Name_1: str = Form(...),
    Serial_Number_1: str = Form(...),
    Device_Description_1: str = Form(...),
    insurance_1: str = Form(...),

    # Device 2..10 (optional)
    AName_2: Optional[str] = Form(None), ASurname_2: Optional[str] = Form(None), APersonnel_nr_2: Optional[str] = Form(None),
    ACompany_2: Optional[str] = Form(None), AClient_Division_2: Optional[str] = Form(None), Device_Name_2: Optional[str] = Form(None),
    Serial_Number_2: Optional[str] = Form(None), Device_Description_2: Optional[str] = Form(None), insurance_2: Optional[str] = Form(None),

    AName_3: Optional[str] = Form(None), ASurname_3: Optional[str] = Form(None), APersonnel_nr_3: Optional[str] = Form(None),
    ACompany_3: Optional[str] = Form(None), AClient_Division_3: Optional[str] = Form(None), Device_Name_3: Optional[str] = Form(None),
    Serial_Number_3: Optional[str] = Form(None), Device_Description_3: Optional[str] = Form(None), insurance_3: Optional[str] = Form(None),

    AName_4: Optional[str] = Form(None), ASurname_4: Optional[str] = Form(None), APersonnel_nr_4: Optional[str] = Form(None),
    ACompany_4: Optional[str] = Form(None), AClient_Division_4: Optional[str] = Form(None), Device_Name_4: Optional[str] = Form(None),
    Serial_Number_4: Optional[str] = Form(None), Device_Description_4: Optional[str] = Form(None), insurance_4: Optional[str] = Form(None),

    AName_5: Optional[str] = Form(None), ASurname_5: Optional[str] = Form(None), APersonnel_nr_5: Optional[str] = Form(None),
    ACompany_5: Optional[str] = Form(None), AClient_Division_5: Optional[str] = Form(None), Device_Name_5: Optional[str] = Form(None),
    Serial_Number_5: Optional[str] = Form(None), Device_Description_5: Optional[str] = Form(None), insurance_5: Optional[str] = Form(None),

    AName_6: Optional[str] = Form(None), ASurname_6: Optional[str] = Form(None), APersonnel_nr_6: Optional[str] = Form(None),
    ACompany_6: Optional[str] = Form(None), AClient_Division_6: Optional[str] = Form(None), Device_Name_6: Optional[str] = Form(None),
    Serial_Number_6: Optional[str] = Form(None), Device_Description_6: Optional[str] = Form(None), insurance_6: Optional[str] = Form(None),

    AName_7: Optional[str] = Form(None), ASurname_7: Optional[str] = Form(None), APersonnel_nr_7: Optional[str] = Form(None),
    ACompany_7: Optional[str] = Form(None), AClient_Division_7: Optional[str] = Form(None), Device_Name_7: Optional[str] = Form(None),
    Serial_Number_7: Optional[str] = Form(None), Device_Description_7: Optional[str] = Form(None), insurance_7: Optional[str] = Form(None),

    AName_8: Optional[str] = Form(None), ASurname_8: Optional[str] = Form(None), APersonnel_nr_8: Optional[str] = Form(None),
    ACompany_8: Optional[str] = Form(None), AClient_Division_8: Optional[str] = Form(None), Device_Name_8: Optional[str] = Form(None),
    Serial_Number_8: Optional[str] = Form(None), Device_Description_8: Optional[str] = Form(None), insurance_8: Optional[str] = Form(None),

    AName_9: Optional[str] = Form(None), ASurname_9: Optional[str] = Form(None), APersonnel_nr_9: Optional[str] = Form(None),
    ACompany_9: Optional[str] = Form(None), AClient_Division_9: Optional[str] = Form(None), Device_Name_9: Optional[str] = Form(None),
    Serial_Number_9: Optional[str] = Form(None), Device_Description_9: Optional[str] = Form(None), insurance_9: Optional[str] = Form(None),

    AName_10: Optional[str] = Form(None), ASurname_10: Optional[str] = Form(None), APersonnel_nr_10: Optional[str] = Form(None),
    ACompany_10: Optional[str] = Form(None), AClient_Division_10: Optional[str] = Form(None), Device_Name_10: Optional[str] = Form(None),
    Serial_Number_10: Optional[str] = Form(None), Device_Description_10: Optional[str] = Form(None), insurance_10: Optional[str] = Form(None),

    db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    # Save VodacomSubscription
    subscription = VodacomSubscription(
        Name_=Name_,
        Surname_=Surname_,
        Personnel_nr=Personnel_nr,
        Company=Company,
        Client_Division=Client_Division,
        Contract_Type=Contract_Type,
        Monthly_Costs=Monthly_Costs,
        VAT=VAT,
        Monthly_Cost_Excl_VAT=Monthly_Cost_Excl_VAT,
        Contract_Term=Contract_Term,
        Sim_Card_Number=Sim_Card_Number,
        Inception_Date=datetime.strptime(Inception_Date, '%Y-%m-%d'),
        Termination_Date=datetime.strptime(Termination_Date, '%Y-%m-%d'),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)  # Get generated id

    # Device 1 (required)
    device_1 = Device(
        vd_id=subscription.id,
        Name_=AName_1,
        Surname_=ASurname_1,
        Personnel_nr=APersonnel_nr_1,
        Company=ACompany_1,
        Client_Division=AClient_Division_1,
        Device_Name=Device_Name_1,
        Serial_Number=Serial_Number_1,
        Device_Description=Device_Description_1,
        insurance=insurance_1
    )
    db.add(device_1)

    # Device 2..10 (optional)
    def maybe_add_device(name, surname, pers, company, division, devname, serial, descr, ins):
        if name:
            db.add(Device(
                vd_id=subscription.id,
                Name_=name,
                Surname_=surname,
                Personnel_nr=pers,
                Company=company,
                Client_Division=division,
                Device_Name=devname,
                Serial_Number=serial,
                Device_Description=descr,
                insurance=ins
            ))

    maybe_add_device(AName_2, ASurname_2, APersonnel_nr_2, ACompany_2, AClient_Division_2,
                     Device_Name_2, Serial_Number_2, Device_Description_2, insurance_2)
    maybe_add_device(AName_3, ASurname_3, APersonnel_nr_3, ACompany_3, AClient_Division_3,
                     Device_Name_3, Serial_Number_3, Device_Description_3, insurance_3)
    maybe_add_device(AName_4, ASurname_4, APersonnel_nr_4, ACompany_4, AClient_Division_4,
                     Device_Name_4, Serial_Number_4, Device_Description_4, insurance_4)
    maybe_add_device(AName_5, ASurname_5, APersonnel_nr_5, ACompany_5, AClient_Division_5,
                     Device_Name_5, Serial_Number_5, Device_Description_5, insurance_5)
    maybe_add_device(AName_6, ASurname_6, APersonnel_nr_6, ACompany_6, AClient_Division_6,
                     Device_Name_6, Serial_Number_6, Device_Description_6, insurance_6)
    maybe_add_device(AName_7, ASurname_7, APersonnel_nr_7, ACompany_7, AClient_Division_7,
                     Device_Name_7, Serial_Number_7, Device_Description_7, insurance_7)
    maybe_add_device(AName_8, ASurname_8, APersonnel_nr_8, ACompany_8, AClient_Division_8,
                     Device_Name_8, Serial_Number_8, Device_Description_8, insurance_8)
    maybe_add_device(AName_9, ASurname_9, APersonnel_nr_9, ACompany_9, AClient_Division_9,
                     Device_Name_9, Serial_Number_9, Device_Description_9, insurance_9)
    maybe_add_device(AName_10, ASurname_10, APersonnel_nr_10, ACompany_10, AClient_Division_10,
                     Device_Name_10, Serial_Number_10, Device_Description_10, insurance_10)

    # Commit all device rows
    db.commit()

    return RedirectResponse("/", status_code=303)

# -------------- JSON APIs --------------


class DeviceOut(BaseModel):
    Name_: str
    Surname_: str
    Personnel_nr: str
    Company: str
    Client_Division: str
    Device_Name: str
    Serial_Number: str
    Device_Description: str
    insurance: str

    class Config:
        from_attributes = True


@app.get("/contracts/{contract_id}/devices", response_model=List[DeviceOut])
def get_devices_for_contract(contract_id: int, db: Session = Depends(get_db)):
    return db.query(Device).filter(Device.vd_id == contract_id).all()


class ContractOut(BaseModel):
    Name_: str
    Surname_: str
    Personnel_nr: str
    Company: str
    Client_Division: str
    Contract_Type: str
    Monthly_Costs: float
    VAT: float
    Monthly_Cost_Excl_VAT: float
    Contract_Term: str
    Inception_Date: Optional[date]
    Termination_Date: Optional[date]

    class Config:
        from_attributes = True


@app.get("/devices/{device_id}/contract", response_model=ContractOut)
def get_contract_for_device(device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device or not device.vd_id:
        return None
    contract = db.query(VodacomSubscription).filter(
        VodacomSubscription.id == device.vd_id).first()
    return contract


# CORS (dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/search/devices")
def search_devices(query: str = "", db: Session = Depends(get_db)):
    results = db.query(Device).filter(
        or_(
            Device.Name_.ilike(f"%{query}%"),
            Device.Surname_.ilike(f"%{query}%"),
            Device.Serial_Number.ilike(f"%{query}%"),
            Device.Device_Name.ilike(f"%{query}%")
        )
    ).limit(20).all()
    return [
        {
            "id": d.id,
            "name": d.Name_,
            "surname": d.Surname_,
            "serial": d.Serial_Number,
            "device": d.Device_Name
        }
        for d in results
    ]


@app.get("/search/contracts")
def search_contracts(query: str, db: Session = Depends(get_db)):
    results = db.query(VodacomSubscription).filter(
        (VodacomSubscription.Name_.ilike(f"%{query}%")) |
        (VodacomSubscription.Surname_.ilike(f"%{query}%")) |
        (VodacomSubscription.Sim_Card_Number.ilike(f"%{query}%"))
    ).limit(20).all()
    return JSONResponse(content=[
        {
            "id": contract.id,
            "name": contract.Name_,
            "surname": contract.Surname_,
            "sim": contract.Sim_Card_Number
        }
        for contract in results
    ])


@app.post("/submit_transfer")
def submit_transfer(
    request: Request,
    selectedDeviceId: Optional[int] = Form(None),
    selectedContractId: Optional[int] = Form(None),
    AName_10: str = Form(...),
    ASurname_10: str = Form(...),
    APersonnel_nr_10: str = Form(...),
    ACompany_10: str = Form(...),
    AClient_Division_10: str = Form(...),
    db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    if selectedDeviceId:
        device = db.query(Device).filter(Device.id == selectedDeviceId).first()
        if not device:
            raise HTTPException(status_code=404, detail="Device not found.")

        # snapshot current owner into Past_device_owners BEFORE update
        db.execute(
            text("""
                INSERT INTO Past_device_owners
                    (d_id, Name_, Surname_, Personnel_nr, Company, Client_Division)
                VALUES
                    (:d_id, :Name_, :Surname_, :Personnel_nr, :Company, :Client_Division)
            """),
            {
                "d_id": device.id,
                "Name_": device.Name_,
                "Surname_": device.Surname_,
                "Personnel_nr": device.Personnel_nr,
                "Company": device.Company,
                "Client_Division": device.Client_Division
            }
        )

        # update with the new owner
        device.Name_ = AName_10
        device.Surname_ = ASurname_10
        device.Personnel_nr = APersonnel_nr_10
        device.Company = ACompany_10
        device.Client_Division = AClient_Division_10

        db.commit()
        return RedirectResponse("/", status_code=303)

    elif selectedContractId:
        contract = db.query(VodacomSubscription).filter(
            VodacomSubscription.id == selectedContractId).first()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found.")

        contract.Name_ = AName_10
        contract.Surname_ = ASurname_10
        contract.Personnel_nr = APersonnel_nr_10
        contract.Company = ACompany_10
        contract.Client_Division = AClient_Division_10

        db.commit()
        return RedirectResponse("/", status_code=303)

    else:
        raise HTTPException(
            status_code=400, detail="No device or contract selected.")

# -------------- DASHBOARD HOME DATA (OPTIONALLY GUARDED) --------------


def month_range(d: date):
    start = date(d.year, d.month, 1)
    if d.month == 12:
        next_start = date(d.year + 1, 1, 1)
    else:
        next_start = date(d.year, d.month + 1, 1)
    return start, next_start


def add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


@app.get("/dashboard/home-data")
def get_home_data(request: Request, db: Session = Depends(get_db)):
    # If you want this JSON locked too, keep this guard. If not, remove the next 2 lines.
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    today = date.today()

    # Block 1: Monthly Costs (current month active contracts)
    month_start, next_month_start = month_range(today)
    current_costs = db.query(func.coalesce(func.sum(VodacomSubscription.Monthly_Costs), 0)).filter(
        VodacomSubscription.Inception_Date <= next_month_start -
        timedelta(days=1),
        VodacomSubscription.Termination_Date >= month_start,
    ).scalar()

    # Costs per month for -3..+3
    months = []
    for offset in range(-3, 4):
        m_start = month_range(add_months(month_start, offset))[0]
        m_next = month_range(add_months(month_start, offset))[1]
        total = db.query(func.coalesce(func.sum(VodacomSubscription.Monthly_Costs), 0)).filter(
            VodacomSubscription.Inception_Date <= m_next - timedelta(days=1),
            VodacomSubscription.Termination_Date >= m_start,
        ).scalar()
        months.append({"month": m_start.strftime(
            "%b %Y"), "total": float(total or 0)})

    # Block 2: Upcoming Terminations (next 3 months)
    three_months_out_start = add_months(month_start, 3)
    term_limit = three_months_out_start - timedelta(days=1)
    upcoming = db.query(
        VodacomSubscription.id,
        VodacomSubscription.Name_,
        VodacomSubscription.Surname_,
        VodacomSubscription.Personnel_nr,
        VodacomSubscription.Company,
        VodacomSubscription.Client_Division,
        VodacomSubscription.Termination_Date,
        VodacomSubscription.due_upgrade
    ).filter(
        VodacomSubscription.Termination_Date >= today,
        VodacomSubscription.Termination_Date <= term_limit
    ).order_by(VodacomSubscription.Termination_Date.asc()).all()

    # Block 3: Devices Overview
    total_devices = db.query(func.count(Device.id)).scalar()
    no_insurance = db.query(func.count(Device.id)).filter(
        or_(
            Device.insurance.is_(None),
            Device.insurance == "",
            func.lower(Device.insurance) == "no"
        )
    ).scalar()
    linked_devices = db.query(func.count(Device.id)).filter(
        Device.vd_id.isnot(None)).scalar()
    transfers_this_month = db.execute(
        text("""
            SELECT COUNT(*) FROM Past_device_owners
            WHERE created_at >= :start AND created_at < :next
        """),
        {"start": datetime.combine(month_start, datetime.min.time()),
         "next":  datetime.combine(next_month_start, datetime.min.time())}
    ).scalar()
    device_stats = {
        "total": int(total_devices or 0),
        "transfers": int(transfers_this_month or 0),
        "no_insurance": int(no_insurance or 0),
        "linked": int(linked_devices or 0),
    }

    # Block 4: Contract Type Breakdown
    type_rows = db.query(
        VodacomSubscription.Contract_Type,
        func.count(VodacomSubscription.id)
    ).group_by(VodacomSubscription.Contract_Type).all()
    contract_breakdown = {
        "labels": [row[0] or "Unknown" for row in type_rows],
        "data":   [int(row[1]) for row in type_rows],
    }

    return {
        "monthly_costs": float(current_costs or 0),
        "months": months,
        "upcoming_terminations": [
            {
                "id": r.id,
                "name": r.Name_,
                "surname": r.Surname_,
                "personnel": r.Personnel_nr,
                "company": r.Company,
                "division": r.Client_Division,
                "termination": r.Termination_Date.strftime("%Y-%m-%d") if r.Termination_Date else None,
                "due_upgrade": r.due_upgrade
            } for r in upcoming
        ],
        "device_stats": device_stats,
        "contract_breakdown": contract_breakdown,
    }


@app.post("/admin/approve/{pending_id}")
def approve_user(
    pending_id: int = Path(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    with db.begin():
        p = db.query(PendingUser).filter(PendingUser.id ==
                                         pending_id).with_for_update().first()
        if not p:
            raise HTTPException(
                status_code=404, detail="Pending user not found")
        user = User(email=p.email, password_hash=p.password_hash,
                    name=p.name, surname=p.surname)
        db.add(user)
        db.delete(p)
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/deny/{pending_id}")
def deny_user(
    pending_id: int = Path(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = db.query(PendingUser).filter(PendingUser.id == pending_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Pending user not found")
    db.delete(p)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/delete/{user_id}")
def delete_user(
    user_id: int = Path(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        return RedirectResponse(url="/admin", status_code=303)

    if u.id == current_user.id:
        raise HTTPException(
            status_code=400, detail="You cannot delete your own account while logged in.")

    db.delete(u)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings(
    request: Request,
    current_user: User = Depends(get_current_user)  # get the logged-in user
):
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "section": "settings",
            "current_user": current_user,
            # for /static cache-busting in your link
            "time": datetime.utcnow().timestamp(),
        },
    )


@app.post("/settings/profile")
def update_profile(
    request: Request,
    name: Optional[str] = Form(None),
    surname: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # load the same DB row into THIS db session
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        # should never happen if you're logged in, but safe-guard anyway
        raise HTTPException(status_code=404, detail="User not found")

    user.name = (name or "").strip() or None
    user.surname = (surname or "").strip() or None

    # email stays unchanged (rendered read-only in the UI)
    db.commit()
    module = request.query_params.get("module")
    if module:
        return RedirectResponse(f"/settings?module={module}&ok=profile", status_code=303)
    return RedirectResponse("/settings?ok=profile", status_code=303)


@app.post("/settings/password")
def update_password(
    request: Request,
    password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 1) Validate inputs
    if new_password != confirm_password:
        # redirect with UI-friendly code (?err=nomatch)
        module = request.query_params.get("module")
        if module:
            return RedirectResponse(f"/settings?module={module}&err=nomatch", status_code=303)
        return RedirectResponse("/settings?err=nomatch", status_code=303)
    if len(new_password) < 8:
        # keep this as a redirect too, or swap to your own message if you like
        module = request.query_params.get("module")
        if module:
            return RedirectResponse(f"/settings?module={module}&err=nomatch", status_code=303)
        return RedirectResponse("/settings?err=nomatch", status_code=303)

    # 2) Verify current password
    if not verify_password(password, current_user.password_hash):
        module = request.query_params.get("module")
        if module:
            return RedirectResponse(f"/settings?module={module}&err=badpwd", status_code=303)
        return RedirectResponse("/settings?err=badpwd", status_code=303)

    # 3) Update in THIS db session
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        # rare, but redirect back safely
        module = request.query_params.get("module")
        if module:
            return RedirectResponse(f"/settings?module={module}", status_code=303)
        return RedirectResponse("/settings", status_code=303)

    user.password_hash = get_password_hash(new_password)
    db.commit()

    # 4) Success
    module = request.query_params.get("module")
    if module:
        return RedirectResponse(f"/settings?module={module}&ok=pwd", status_code=303)
    return RedirectResponse("/settings?ok=pwd", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    # Reuse login card styling
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@app.post("/register", response_class=HTMLResponse)
def register_post(
    request: Request,
    name: str = Form(...),
    surname: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    # Basic checks
    if password != confirm_password:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Passwords do not match."}, status_code=400)

    # Check email not in users nor pending
    existing_user = db.query(User).filter(User.email == email).first()
    existing_pending = db.query(PendingUser).filter(
        PendingUser.email == email).first()
    if existing_user or existing_pending:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already exists or is pending approval."}, status_code=400)

    hashed = get_password_hash(password)
    pending = PendingUser(email=email.strip().lower(
    ), password_hash=hashed, name=name.strip(), surname=surname.strip())
    db.add(pending)
    db.commit()

    # After submission, send them back to login with a friendly note
    return templates.TemplateResponse("login.html", {"request": request, "error": "Account request submitted. An admin will approve or deny."}, status_code=200)


# === Devices API (fetch + patch) ===


@app.get("/api/devices/{device_id}")
def api_get_device(device_id: int, request: Request, db: Session = Depends(get_db)):
    # session-guard like the rest of your app
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")

    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Return the fields you show in the dashboard
    return {
        "id": device.id,
        "Name_": device.Name_,
        "Surname_": device.Surname_,
        "Personnel_nr": device.Personnel_nr,
        "Company": device.Company,
        "Client_Division": device.Client_Division,
        "Device_Name": device.Device_Name,
        "Serial_Number": device.Serial_Number,
        "Device_Description": device.Device_Description,
        "insurance": device.insurance,
        "vd_id": getattr(device, "vd_id", None),
    }


@app.put("/api/devices/{device_id}")
def api_update_device(
    device_id: int,
    request: Request,
    # e.g. {"Company": "PCM", "Device_Name": "New name"}
    updates: dict = Body(...),
    db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")

    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Only allow known fields
    allowed = {
        "Name_",
        "Surname_",
        "Personnel_nr",
        "Company",
        "Client_Division",
        "Device_Name",
        "Serial_Number",
        "Device_Description",
        "insurance",
    }
    changed = {}
    for k, v in updates.items():
        if k in allowed:
            setattr(device, k, v)
            changed[k] = v

    if not changed:
        return {"updated": False, "message": "No valid fields provided."}

    db.add(device)
    db.commit()
    return {"updated": True, "id": device.id, "changed": changed}


ALLOWED_DEVICE_FIELDS = {
    "Name_", "Surname_", "Personnel_nr", "Company", "Client_Division",
    "Device_Name", "Serial_Number", "Device_Description", "insurance"
}

ALLOWED_CONTRACT_FIELDS = {
    "Name_", "Surname_", "Personnel_nr", "Company", "Client_Division",
    "Contract_Type", "Monthly_Costs", "VAT", "Monthly_Cost_Excl_VAT",
    "Contract_Term", "Inception_Date", "Termination_Date", "Sim_Card_Number", "due_upgrade"
}


def _current_user_email(db: Session, request: Request) -> str:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    u = db.query(User).filter(User.id == uid).first()
    if not u:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return u.email


@app.post("/api/edit-requests")
def create_device_edit_request(
    request: Request,
    # { "device_id": 123, "changes": { "Company": "PCM", ... } }
    payload: dict = Body(...),
    db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")

    device_id = payload.get("device_id")
    changes = payload.get("changes") or {}
    if not device_id or not isinstance(changes, dict) or not changes:
        raise HTTPException(
            status_code=400, detail="Missing device_id or changes")

    # validate device exists
    dev = db.query(Device).filter(Device.id == device_id).first()
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")

    # filter to allowed fields only
    cleaned = {k: v for k, v in changes.items() if k in ALLOWED_DEVICE_FIELDS}
    if not cleaned:
        raise HTTPException(status_code=400, detail="No valid fields provided")

    req = DeviceEditRequest(
        device_id=device_id,
        requester_email=_current_user_email(db, request),
        changes_json=json.dumps(cleaned, ensure_ascii=False)
    )
    db.add(req)
    db.commit()
    return {"queued": True, "request_id": req.id}


@app.post("/admin/edit-requests/{req_id}/approve")
def approve_edit_request(
    req_id: int = Path(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    req = db.query(DeviceEditRequest).filter(
        DeviceEditRequest.id == req_id).with_for_update().first()
    if not req:
        raise HTTPException(status_code=404, detail="Edit request not found")

    dev = db.query(Device).filter(
        Device.id == req.device_id).with_for_update().first()
    if not dev:
        db.delete(req)
        db.commit()
        raise HTTPException(status_code=404, detail="Device not found")

    changes = json.loads(req.changes_json or "{}")
    for k, v in changes.items():
        if k in ALLOWED_DEVICE_FIELDS:
            setattr(dev, k, v)

    req.status = "approved"
    req.processed_by = current_user.id
    req.processed_at = datetime.utcnow()
    db.add(dev)
    db.add(req)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/api/devices/{device_id}/edit-requests")
def create_edit_request(
    device_id: int,
    request: Request,
    updates: dict = Body(...),
    db: Session = Depends(get_db)
):
    # Require login like your other APIs
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Basic device check
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Get requester email from current user
    current_user_id = request.session.get("user_id")
    user = db.query(User).filter(User.id == current_user_id).first()
    requester_email = user.email if user else "unknown@local"

    # Only allow known fields (same allowlist you already use)
    allowed = {
        "Name_", "Surname_", "Personnel_nr", "Company", "Client_Division",
        "Device_Name", "Serial_Number", "Device_Description", "insurance",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return {"created": False, "message": "No valid fields provided."}

    req = DeviceEditRequest(
        device_id=device_id,
        requester_email=requester_email,
        changes_json=json.dumps(filtered),
        status="pending"
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return {"created": True, "request_id": req.id}


@app.get("/admin", response_class=HTMLResponse)
def admin(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    pending = db.query(PendingUser).order_by(
        PendingUser.created_at.desc()).all()
    users = db.query(User).order_by(User.created_at.desc()).all()

    device_reqs = db.query(DeviceEditRequest)\
        .filter(DeviceEditRequest.status == "pending")\
        .order_by(DeviceEditRequest.created_at.desc()).all()
    for r in device_reqs:
        r.kind = "device"
        r.ref_id = r.device_id
        r.changes = json.loads(r.changes_json or "{}")

    contract_reqs = db.query(ContractEditRequest)\
        .filter(ContractEditRequest.status == "pending")\
        .order_by(ContractEditRequest.created_at.desc()).all()
    for r in contract_reqs:
        r.kind = "contract"
        r.ref_id = r.contract_id
        r.changes = json.loads(r.changes_json or "{}")

    # Merge & sort newest first
    edit_reqs = sorted([*device_reqs, *contract_reqs],
                       key=lambda x: x.created_at or datetime.min, reverse=True)

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "section": "admin",
        "pending": pending,
        "users": users,
        "edit_requests": edit_reqs,
        "time": datetime.utcnow().timestamp(),
        "current_user": current_user,
    })


@app.post("/admin/edit-requests/{req_id}/deny")
def deny_edit_request(
    req_id: int = Path(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    req = db.query(DeviceEditRequest).filter(
        DeviceEditRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Edit request not found")
    req.status = "denied"
    req.processed_by = current_user.id
    req.processed_at = datetime.utcnow()
    db.add(req)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/api/contracts/{contract_id}/edit-requests")
def create_contract_edit_request(
    contract_id: int,
    request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")

    contract = db.query(VodacomSubscription).filter(
        VodacomSubscription.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    # Filter fields to allow list
    cleaned = {k: v for (k, v) in (payload or {}).items()
               if k in ALLOWED_CONTRACT_FIELDS}
    if not cleaned:
        return {"created": False, "message": "No valid fields provided."}

    # If dates come in as strings, we keep them as strings in JSON; we only parse on approve
    req = ContractEditRequest(
        contract_id=contract_id,
        requester_email=_current_user_email(db, request),
        changes_json=json.dumps(cleaned, ensure_ascii=False),
        status="pending"
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return {"created": True, "request_id": req.id}


@app.post("/admin/contract-edit-requests/{req_id}/approve")
def approve_contract_edit_request(
    req_id: int = Path(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    req = db.query(ContractEditRequest).filter(
        ContractEditRequest.id == req_id).first()
    if not req or req.status != "pending":
        raise HTTPException(
            status_code=404, detail="Request not found or already processed")

    contract = db.query(VodacomSubscription).filter(
        VodacomSubscription.id == req.contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    changes = json.loads(req.changes_json or "{}")

    def _parse_date(s):
        if not s:
            return None
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    for k, v in changes.items():
        if k in ("Inception_Date", "Termination_Date") and isinstance(v, str):
            v = _parse_date(v)
        setattr(contract, k, v)

    req.status = "approved"
    req.processed_by = current_user.id
    req.processed_at = datetime.utcnow()
    db.add(contract)
    db.add(req)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/contract-edit-requests/{req_id}/deny")
def deny_contract_edit_request(
    req_id: int = Path(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    req = db.query(ContractEditRequest).filter(
        ContractEditRequest.id == req_id
    ).first()
    if not req or req.status != "pending":
        raise HTTPException(
            status_code=404, detail="Request not found or already processed")

    req.status = "denied"
    req.processed_by = current_user.id
    req.processed_at = datetime.utcnow()
    db.add(req)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/{user_id}/make-admin")
def make_admin(
    user_id: int = Path(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if not u.is_admin:
        u.is_admin = True
        db.commit()
        if request and current_user.id == u.id:
            request.session["is_admin"] = True
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/{user_id}/revoke-admin")
def revoke_admin(
    user_id: int = Path(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if u.is_admin:
        admin_count = db.query(func.count(User.id)).filter(
            User.is_admin == True).scalar() or 0
        if admin_count <= 1:
            raise HTTPException(
                status_code=400, detail="Cannot revoke the last remaining admin.")
        u.is_admin = False
        db.commit()
        if request and current_user.id == u.id:
            request.session["is_admin"] = False
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/")
async def biometric_root_catch(request: Request):
    raw_bytes = await request.body()
    headers = dict(request.headers)

    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).isoformat()

    with open("/var/www/pcm_tracker/biometric_raw.log", "ab") as f:
        f.write(f"\n--- {stamp} UTC ---\n".encode())
        f.write(f"Client: {request.client}\n".encode())
        f.write(f"Headers: {headers}\n".encode())
        f.write(b"Body:\n")
        f.write(raw_bytes[:10000])
        f.write(b"\n")

    print(f"[BIOMETRIC ROOT] received {len(raw_bytes)} bytes")
    return {"ok": True}


"""
LOCAL RUN CHECKLIST (WINDOWS / POWERSHELL)

1) Go into the project folder
   cd "C:\\Users\\Henricus\\OneDrive - Professional\\Desktop\\PCM_Tracer"

2) Activate the virtual environment (you must see (venv))
   .\\venv\\Scripts\\Activate.ps1

   If this file does not exist, the venv is missing and must be recreated.

3) Set local mode (IMPORTANT)
   $env:APP_ENV="local"

4) Confirm the environment variable is set
   echo $env:APP_ENV

   It MUST output:
   local

5) Run the app
   uvicorn main:app --reload

NOTE:
- If APP_ENV is NOT set to "local", the app will try to use MySQL
  and will fail on a local machine.
- Environment variables do NOT persist between terminals or reboots.
"""
