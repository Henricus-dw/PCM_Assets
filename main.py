from fastapi import Body
from sqlalchemy import desc   # already imported at top, but make sure
from datetime import datetime  # (already imported above in your file)
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

from auth import get_current_user

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
    request.session["user_id"] = user.id  # signed, HttpOnly cookie
    return RedirectResponse(url="/", status_code=302)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)

# -------------- PAGE ROUTES (ALL GUARDED) --------------
# 1) HOME "/" -> form.html


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("dashboard_home.html", {"request": request, "section": "home"})


# 2) DASHBOARD "/" base page (you already had this)


@app.get("/form", response_class=HTMLResponse)
def form_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("form.html",
                                      {"request": request, "section": "form"})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_home(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard_home.html",
        {"request": request, "section": "home"}
    )

# 3) DASHBOARD HOME explicit page at /dashboard/home (you asked for this separately)


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
def approve_user(pending_id: int = Path(...), db: Session = Depends(get_db), request: Request = None):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    # Move inside a transaction so it's atomic
    with db.begin():
        p = db.query(PendingUser).filter(PendingUser.id ==
                                         pending_id).with_for_update().first()
        if not p:
            raise HTTPException(
                status_code=404, detail="Pending user not found")

        # Create real User (will 409 if email taken)
        user = User(email=p.email, password_hash=p.password_hash,
                    name=p.name, surname=p.surname)
        db.add(user)
        db.delete(p)

    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/deny/{pending_id}")
def deny_user(pending_id: int = Path(...), db: Session = Depends(get_db), request: Request = None):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

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
    request: Request = None
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    # Optional: prevent deleting yourself
    current_user_id = request.session.get("user_id")

    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        # Nothing to delete; just go back
        return RedirectResponse(url="/admin", status_code=303)

    if u.id == current_user_id:
        # You can change to a friendlier UI flow if you prefer
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
        return RedirectResponse("/settings?err=nomatch", status_code=303)
    if len(new_password) < 8:
        # keep this as a redirect too, or swap to your own message if you like
        return RedirectResponse("/settings?err=nomatch", status_code=303)

    # 2) Verify current password
    if not verify_password(password, current_user.password_hash):
        return RedirectResponse("/settings?err=badpwd", status_code=303)

    # 3) Update in THIS db session
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        # rare, but redirect back safely
        return RedirectResponse("/settings", status_code=303)

    user.password_hash = get_password_hash(new_password)
    db.commit()

    # 4) Success
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
def approve_edit_request(req_id: int, request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    req = db.query(DeviceEditRequest).filter(
        DeviceEditRequest.id == req_id).with_for_update().first()
    if not req:
        raise HTTPException(status_code=404, detail="Edit request not found")

    dev = db.query(Device).filter(
        Device.id == req.device_id).with_for_update().first()
    if not dev:
        # drop invalid queue item
        db.delete(req)
        db.commit()
        raise HTTPException(status_code=404, detail="Device not found")

    changes = json.loads(req.changes_json or "{}")
    for k, v in changes.items():
        if k in ALLOWED_DEVICE_FIELDS:
            setattr(dev, k, v)

    db.add(dev)
    db.delete(req)
    db.commit()

    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/edit-requests/{req_id}/deny")
def deny_edit_request(req_id: int, request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    req = db.query(DeviceEditRequest).filter(
        DeviceEditRequest.id == req_id).first()
    if req:
        db.delete(req)
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
def admin(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

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
    })


@app.post("/admin/edit-requests/{req_id}/approve")
def approve_edit_request(
    req_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    req = db.query(DeviceEditRequest).filter(
        DeviceEditRequest.id == req_id).first()
    if not req or req.status != "pending":
        raise HTTPException(
            status_code=404, detail="Request not found or already processed")

    device = db.query(Device).filter(Device.id == req.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Apply changes
    changes = json.loads(req.changes_json)
    for k, v in changes.items():
        setattr(device, k, v)

    db.add(device)
    # mark request processed
    req.status = "approved"
    req.processed_by = request.session.get("user_id")
    req.processed_at = datetime.utcnow()
    db.add(req)
    db.commit()

    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/edit-requests/{req_id}/deny")
def deny_edit_request(
    req_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    req = db.query(DeviceEditRequest).filter(
        DeviceEditRequest.id == req_id).first()
    if not req or req.status != "pending":
        raise HTTPException(
            status_code=404, detail="Request not found or already processed")

    req.status = "denied"
    req.processed_by = request.session.get("user_id")
    req.processed_at = datetime.utcnow()
    db.add(req)
    db.commit()


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
def approve_contract_edit_request(req_id: int, request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    req = db.query(ContractEditRequest).filter(
        ContractEditRequest.id == req_id).first()
    if not req or req.status != "pending":
        raise HTTPException(
            status_code=404, detail="Request not found or already processed")

    contract = db.query(VodacomSubscription).filter(
        VodacomSubscription.id == req.contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    changes = json.loads(req.changes_json) if req.changes_json else {}
    # Apply, parsing dates if needed
    for k, v in changes.items():
        if k in ("Inception_Date", "Termination_Date") and isinstance(v, str) and v.strip():
            try:
                v = datetime.strptime(v[:10], "%Y-%m-%d")
            except Exception:
                pass
        setattr(contract, k, v)

    req.status = "approved"
    req.processed_by = request.session.get("user_id")
    req.processed_at = datetime.utcnow()

    db.add(contract)
    db.add(req)
    db.commit()

    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/contract-edit-requests/{req_id}/deny")
def deny_contract_edit_request(req_id: int, request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    req = db.query(ContractEditRequest).filter(
        ContractEditRequest.id == req_id).first()
    if not req or req.status != "pending":
        raise HTTPException(
            status_code=404, detail="Request not found or already processed")

    req.status = "denied"
    req.processed_by = request.session.get("user_id")
    req.processed_at = datetime.utcnow()
    db.add(req)
    db.commit()

    return RedirectResponse(url="/admin", status_code=303)

# ---------------------------------------------------------------------------
# Contract edit request approval/denial (Vodacom)
# ---------------------------------------------------------------------------


@app.post("/admin/contract-edit-requests/{req_id}/approve")
def approve_contract_edit_request(
    req_id: int, request: Request, db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    req = db.query(ContractEditRequest).filter(
        ContractEditRequest.id == req_id).first()
    if not req or req.status != "pending":
        raise HTTPException(
            status_code=404, detail="Request not found or already processed")

    contract = (
        db.query(VodacomSubscription)
        .filter(VodacomSubscription.id == req.contract_id)
        .first()
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    changes = json.loads(req.changes_json or "{}")

    def _parse_date(s):
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None

    for k, v in changes.items():
        if k in ("Inception_Date", "Termination_Date") and isinstance(v, str):
            v = _parse_date(v)
        setattr(contract, k, v)

    db.add(contract)
    req.status = "approved"
    req.processed_by = request.session.get("user_id")
    req.processed_at = datetime.utcnow()
    db.add(req)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/contract-edit-requests/{req_id}/deny")
def deny_contract_edit_request(
    req_id: int, request: Request, db: Session = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)

    req = db.query(ContractEditRequest).filter(
        ContractEditRequest.id == req_id).first()
    if not req or req.status != "pending":
        raise HTTPException(
            status_code=404, detail="Request not found or already processed")

    req.status = "denied"
    req.processed_by = request.session.get("user_id")
    req.processed_at = datetime.utcnow()
    db.add(req)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)
