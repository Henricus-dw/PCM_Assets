from fastapi import Form
from typing import Optional
from fastapi import FastAPI, Request, Form, Depends, status, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from models import VodacomSubscription
from models import Device
from database import SessionLocal, engine, Base
from pydantic import BaseModel
from datetime import date
# Create all tables (only needed once)
Base.metadata.create_all(bind=engine)

app = FastAPI()

# Mount static folder (for CSS/JS if needed)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Set template directory
templates = Jinja2Templates(directory="templates")
# Ensures templates are reloaded on every request
templates.env.auto_reload = True
templates.env.cache = {}               # Clears internal Jinja2 cache
templates.env.cache_size = 0           # Prevents storing any templates in cache


# Dependency to get DB session


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    return templates.TemplateResponse("form.html", {"request": request, "section": "form"})


@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.post("/submit", response_class=RedirectResponse)
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
    db: Session = Depends(get_db)  # ✅ FIXED: Correctly inject DB session
):
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


@app.get("/dashboard/vodacom", response_class=HTMLResponse)
def dashboard_vodacom(request: Request):
    db: Session = SessionLocal()

    # Get all subscriptions
    records = db.query(VodacomSubscription).order_by(
        VodacomSubscription.id.desc()).all()

    # Attach devices to each subscription
    for record in records:
        record.devices = db.query(Device).filter(
            Device.vd_id == record.id).all()

    db.close()

    return templates.TemplateResponse(
        "dashboard_vodacom.html",
        {"request": request, "records": records, "section": "vodacom"}
    )


@app.get("/dashboard/devices", response_class=HTMLResponse)
def dashboard_devices(request: Request):
    db: Session = SessionLocal()

    devices = db.query(Device).order_by(Device.id.desc()).all()

    db.close()

    return templates.TemplateResponse(
        "dashboard_devices.html",
        {"request": request, "devices": devices, "section": "devices"}
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_home(request: Request):
    return templates.TemplateResponse(
        "dashboard_home.html",
        {"request": request, "section": "home"}
    )


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

    # After submission, redirect back to the main form or anywhere appropriate
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

    # Device 2 fields (optional)
    AName_2: Optional[str] = Form(None),
    ASurname_2: Optional[str] = Form(None),
    APersonnel_nr_2: Optional[str] = Form(None),
    ACompany_2: Optional[str] = Form(None),
    AClient_Division_2: Optional[str] = Form(None),
    Device_Name_2: Optional[str] = Form(None),
    Serial_Number_2: Optional[str] = Form(None),
    Device_Description_2: Optional[str] = Form(None),
    insurance_2: Optional[str] = Form(None),

    # Device 3 fields (optional)
    AName_3: Optional[str] = Form(None),
    ASurname_3: Optional[str] = Form(None),
    APersonnel_nr_3: Optional[str] = Form(None),
    ACompany_3: Optional[str] = Form(None),
    AClient_Division_3: Optional[str] = Form(None),
    Device_Name_3: Optional[str] = Form(None),
    Serial_Number_3: Optional[str] = Form(None),
    Device_Description_3: Optional[str] = Form(None),
    insurance_3: Optional[str] = Form(None),

    # Device 4 fields (optional)
    AName_4: Optional[str] = Form(None),
    ASurname_4: Optional[str] = Form(None),
    APersonnel_nr_4: Optional[str] = Form(None),
    ACompany_4: Optional[str] = Form(None),
    AClient_Division_4: Optional[str] = Form(None),
    Device_Name_4: Optional[str] = Form(None),
    Serial_Number_4: Optional[str] = Form(None),
    Device_Description_4: Optional[str] = Form(None),
    insurance_4: Optional[str] = Form(None),

    # Device 5 fields (optional)
    AName_5: Optional[str] = Form(None),
    ASurname_5: Optional[str] = Form(None),
    APersonnel_nr_5: Optional[str] = Form(None),
    ACompany_5: Optional[str] = Form(None),
    AClient_Division_5: Optional[str] = Form(None),
    Device_Name_5: Optional[str] = Form(None),
    Serial_Number_5: Optional[str] = Form(None),
    Device_Description_5: Optional[str] = Form(None),
    insurance_5: Optional[str] = Form(None),

    # Device 6 fields (optional)
    AName_6: Optional[str] = Form(None),
    ASurname_6: Optional[str] = Form(None),
    APersonnel_nr_6: Optional[str] = Form(None),
    ACompany_6: Optional[str] = Form(None),
    AClient_Division_6: Optional[str] = Form(None),
    Device_Name_6: Optional[str] = Form(None),
    Serial_Number_6: Optional[str] = Form(None),
    Device_Description_6: Optional[str] = Form(None),
    insurance_6: Optional[str] = Form(None),

    # Device 7 fields (optional)
    AName_7: Optional[str] = Form(None),
    ASurname_7: Optional[str] = Form(None),
    APersonnel_nr_7: Optional[str] = Form(None),
    ACompany_7: Optional[str] = Form(None),
    AClient_Division_7: Optional[str] = Form(None),
    Device_Name_7: Optional[str] = Form(None),
    Serial_Number_7: Optional[str] = Form(None),
    Device_Description_7: Optional[str] = Form(None),
    insurance_7: Optional[str] = Form(None),

    # Device 8 fields (optional)
    AName_8: Optional[str] = Form(None),
    ASurname_8: Optional[str] = Form(None),
    APersonnel_nr_8: Optional[str] = Form(None),
    ACompany_8: Optional[str] = Form(None),
    AClient_Division_8: Optional[str] = Form(None),
    Device_Name_8: Optional[str] = Form(None),
    Serial_Number_8: Optional[str] = Form(None),
    Device_Description_8: Optional[str] = Form(None),
    insurance_8: Optional[str] = Form(None),

    # Device 9 fields (optional)
    AName_9: Optional[str] = Form(None),
    ASurname_9: Optional[str] = Form(None),
    APersonnel_nr_9: Optional[str] = Form(None),
    ACompany_9: Optional[str] = Form(None),
    AClient_Division_9: Optional[str] = Form(None),
    Device_Name_9: Optional[str] = Form(None),
    Serial_Number_9: Optional[str] = Form(None),
    Device_Description_9: Optional[str] = Form(None),
    insurance_9: Optional[str] = Form(None),

    # Device 10 fields (optional)
    AName_10: Optional[str] = Form(None),
    ASurname_10: Optional[str] = Form(None),
    APersonnel_nr_10: Optional[str] = Form(None),
    ACompany_10: Optional[str] = Form(None),
    AClient_Division_10: Optional[str] = Form(None),
    Device_Name_10: Optional[str] = Form(None),
    Serial_Number_10: Optional[str] = Form(None),
    Device_Description_10: Optional[str] = Form(None),
    insurance_10: Optional[str] = Form(None),

    db: Session = Depends(get_db)
):
    from datetime import datetime

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

    # Device 2 (optional)
    if AName_2:
        device_2 = Device(
            vd_id=subscription.id,
            Name_=AName_2,
            Surname_=ASurname_2,
            Personnel_nr=APersonnel_nr_2,
            Company=ACompany_2,
            Client_Division=AClient_Division_2,
            Device_Name=Device_Name_2,
            Serial_Number=Serial_Number_2,
            Device_Description=Device_Description_2,
            insurance=insurance_2
        )
        db.add(device_2)

    # Device 3 (optional)
    if AName_3:
        device_3 = Device(
            vd_id=subscription.id,
            Name_=AName_3,
            Surname_=ASurname_3,
            Personnel_nr=APersonnel_nr_3,
            Company=ACompany_3,
            Client_Division=AClient_Division_3,
            Device_Name=Device_Name_3,
            Serial_Number=Serial_Number_3,
            Device_Description=Device_Description_3,
            insurance=insurance_3
        )
        db.add(device_3)

    # Device 4 (optional)
    if AName_4:
        device_4 = Device(
            vd_id=subscription.id,
            Name_=AName_4,
            Surname_=ASurname_4,
            Personnel_nr=APersonnel_nr_4,
            Company=ACompany_4,
            Client_Division=AClient_Division_4,
            Device_Name=Device_Name_4,
            Serial_Number=Serial_Number_4,
            Device_Description=Device_Description_4,
            insurance=insurance_4
        )
        db.add(device_4)

    # Device 5 (optional)
    if AName_5:
        device_5 = Device(
            vd_id=subscription.id,
            Name_=AName_5,
            Surname_=ASurname_5,
            Personnel_nr=APersonnel_nr_5,
            Company=ACompany_5,
            Client_Division=AClient_Division_5,
            Device_Name=Device_Name_5,
            Serial_Number=Serial_Number_5,
            Device_Description=Device_Description_5,
            insurance=insurance_5
        )
        db.add(device_5)

    # Device 6 (optional)
    if AName_6:
        device_6 = Device(
            vd_id=subscription.id,
            Name_=AName_6,
            Surname_=ASurname_6,
            Personnel_nr=APersonnel_nr_6,
            Company=ACompany_6,
            Client_Division=AClient_Division_6,
            Device_Name=Device_Name_6,
            Serial_Number=Serial_Number_6,
            Device_Description=Device_Description_6,
            insurance=insurance_6
        )
        db.add(device_6)

    # Device 7 (optional)
    if AName_7:
        device_7 = Device(
            vd_id=subscription.id,
            Name_=AName_7,
            Surname_=ASurname_7,
            Personnel_nr=APersonnel_nr_7,
            Company=ACompany_7,
            Client_Division=AClient_Division_7,
            Device_Name=Device_Name_7,
            Serial_Number=Serial_Number_7,
            Device_Description=Device_Description_7,
            insurance=insurance_7
        )
        db.add(device_7)

    # Device 8 (optional)
    if AName_8:
        device_8 = Device(
            vd_id=subscription.id,
            Name_=AName_8,
            Surname_=ASurname_8,
            Personnel_nr=APersonnel_nr_8,
            Company=ACompany_8,
            Client_Division=AClient_Division_8,
            Device_Name=Device_Name_8,
            Serial_Number=Serial_Number_8,
            Device_Description=Device_Description_8,
            insurance=insurance_8
        )
        db.add(device_8)

    # Device 9 (optional)
    if AName_9:
        device_9 = Device(
            vd_id=subscription.id,
            Name_=AName_9,
            Surname_=ASurname_9,
            Personnel_nr=APersonnel_nr_9,
            Company=ACompany_9,
            Client_Division=AClient_Division_9,
            Device_Name=Device_Name_9,
            Serial_Number=Serial_Number_9,
            Device_Description=Device_Description_9,
            insurance=insurance_9
        )
        db.add(device_9)

    # Device 10 (optional)
    if AName_10:
        device_10 = Device(
            vd_id=subscription.id,
            Name_=AName_10,
            Surname_=ASurname_10,
            Personnel_nr=APersonnel_nr_10,
            Company=ACompany_10,
            Client_Division=AClient_Division_10,
            Device_Name=Device_Name_10,
            Serial_Number=Serial_Number_10,
            Device_Description=Device_Description_10,
            insurance=insurance_10
        )
        db.add(device_10)

    # Commit all device rows
    db.commit()

    return RedirectResponse("/", status_code=303)


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
    # list only the fields you want to show
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
    Inception_Date: str
    Inception_Date: Optional[date]
    Termination_Date: Optional[date]

    class Config:
        from_attributes = True  # using Pydantic v2


@app.get("/devices/{device_id}/contract", response_model=ContractOut)
def get_contract_for_device(device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device or not device.vd_id:
        return None  # no contract linked
    contract = db.query(VodacomSubscription).filter(
        VodacomSubscription.id == device.vd_id).first()
    return contract
