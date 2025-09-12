from sqlalchemy import text, Column, Integer, String, Float, DateTime, func, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session
from fastapi import FastAPI, Request, Form, Depends, HTTPException
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

# ---- Your models & DB ----
from models import VodacomSubscription, Device, User
from database import SessionLocal, engine, Base

# Create all tables (only needed once)
Base.metadata.create_all(bind=engine)

# ---- App setup ----
app = FastAPI()

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


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request, "section": "admin"})


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request, "section": "settings"})
