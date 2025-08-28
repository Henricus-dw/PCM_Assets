from fastapi import FastAPI, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime

from models import VodacomSubscription
from models import Device
from database import SessionLocal, engine, Base

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


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    db = SessionLocal()
    records = db.query(VodacomSubscription).order_by(
        VodacomSubscription.id.desc()).all()

    db.close()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "records": records, "section": "dashboard"}
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
    # --- Vodacom Subscription fields ---
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

    # --- Device form fields ---
    Device_Name: str = Form(...),
    Serial_Number: str = Form(...),
    Device_Description: str = Form(...),
    insurance: str = Form(...),
    # Duplicate info reused
    AName_: str = Form(...),
    ASurname_: str = Form(...),
    APersonnel_nr: str = Form(...),
    ACompany: str = Form(...),
    AClient_Division: str = Form(...),

    db: Session = Depends(get_db)
):
    # Step 1: Save the VodacomSubscription
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
    db.refresh(subscription)  # 💡 Gets the generated ID

    # Step 2: Save the Device
    device = Device(
        vd_id=subscription.id,  # Link to the subscription ID
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

    return RedirectResponse("/", status_code=303)
