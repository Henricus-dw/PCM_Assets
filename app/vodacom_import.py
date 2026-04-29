import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from io import BytesIO

from sqlalchemy import text
from sqlalchemy.orm import Session

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
PLACEHOLDER_VALUES = {"", "n/a", "na", "none", "no device", "nill", "nil"}
DEVICE_TYPE_MAP = {
    "phone": "Cell-Phone",
    "router": "Router",
    "tracker": "Tracker",
    "tablet": "Tablet",
    "laptop": "Laptop",
    "scanpad": "Other",
}


class ImportValidationError(Exception):
    pass


def col_to_idx(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def parse_excel_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        n = int(float(raw))
        if n <= 0:
            return None
        return (datetime(1899, 12, 30) + timedelta(days=n)).date()
    except (TypeError, ValueError):
        return None


def parse_amount(raw: str) -> float | None:
    if not raw:
        return None
    cleaned = re.sub(r"[R,\s]", "", str(raw))
    try:
        return float(cleaned)
    except ValueError:
        return None


def split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], ""
    return "", ""


def is_placeholder(val: str) -> bool:
    return str(val).strip().lower() in PLACEHOLDER_VALUES


def normalize_device_description(raw: str) -> str:
    key = raw.strip().lower()
    return DEVICE_TYPE_MAP.get(key, raw.strip() or "Other")


def infer_contract_type(plan_name: str) -> str:
    lower = plan_name.lower().replace(" ", "")
    for k in ("data", "gb", "machine2machine", "m2m"):
        if k in lower:
            return "DATA"
    return "AIRTIME"


def read_excel_rows(file_bytes: bytes) -> list[dict]:
    shared: list[str] = []
    rows_out: list[dict] = []

    with zipfile.ZipFile(BytesIO(file_bytes)) as z:
        if "xl/sharedStrings.xml" in z.namelist():
            sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sst.findall("m:si", NS):
                t = si.find("m:t", NS)
                if t is not None:
                    shared.append(t.text or "")
                else:
                    shared.append("".join((r.text or "")
                                  for r in si.findall("m:r/m:t", NS)))

        wb = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = wb.findall(".//m:sheets/m:sheet", NS)

        for i, sh in enumerate(sheets, start=1):
            sheet_name = sh.attrib.get("name", f"Sheet{i}")
            ws = ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))
            data_rows = ws.findall("m:sheetData/m:row", NS)

            for row in data_rows[1:]:
                vals = [""] * 21

                for c in row.findall("m:c", NS):
                    m = re.match(r"([A-Z]+)", c.attrib.get("r", ""))
                    if not m:
                        continue
                    ci = col_to_idx(m.group(1))
                    if not 0 <= ci < 21:
                        continue

                    ctype = c.attrib.get("t")
                    v_el = c.find("m:v", NS)

                    if ctype == "s" and v_el is not None and v_el.text:
                        idx = int(v_el.text)
                        vals[ci] = shared[idx] if idx < len(shared) else ""
                    elif ctype == "inlineStr":
                        t_el = c.find("m:is/m:t", NS)
                        vals[ci] = t_el.text if t_el is not None and t_el.text else ""
                    else:
                        vals[ci] = v_el.text if v_el is not None and v_el.text else ""

                rows_out.append(
                    {
                        "sheet": sheet_name,
                        "acc_number": vals[0].strip(),
                        "account_name": vals[1].strip(),
                        "employee_name": vals[2].strip(),
                        "contract_number": vals[3].strip(),
                        "monthly_amount_raw": vals[4].strip(),
                        "plan_name": vals[5].strip(),
                        "cur_device_type": vals[6].strip(),
                        "cur_device_make": vals[7].strip(),
                        "cur_device_model": vals[8].strip(),
                        "cur_device_serial": vals[9].strip(),
                        "start_date_raw": vals[10].strip(),
                        "end_date_raw": vals[11].strip(),
                        "contract_term_raw": vals[12].strip(),
                        "sim_number": vals[13].strip(),
                        "sim_device_flag": vals[14].strip(),
                        "last_used_raw": vals[15].strip(),
                        "iss_device_type": vals[16].strip(),
                        "iss_device_make": vals[17].strip(),
                        "iss_device_model": vals[18].strip(),
                        "iss_device_serial": vals[19].strip(),
                        "iss_date_raw": vals[20].strip(),
                    }
                )

    return rows_out


def _check_schema(session: Session) -> None:
    cols_result = session.execute(
        text("SHOW COLUMNS FROM Vodacom_subscription"))
    cols = {row[0] for row in cols_result.fetchall()}
    missing = {"account_name", "last_used_date"} - cols
    if missing:
        raise ImportValidationError(
            "Missing required columns on Vodacom_subscription: " +
            ", ".join(sorted(missing))
        )

    table_result = session.execute(text("SHOW TABLES LIKE 'device_issuances'"))
    if not table_result.fetchone():
        raise ImportValidationError("Missing required table: device_issuances")


def import_excel_bytes(session: Session, file_bytes: bytes) -> dict:
    try:
        rows = read_excel_rows(file_bytes)
    except zipfile.BadZipFile as exc:
        raise ImportValidationError("Invalid Excel file format") from exc

    _check_schema(session)

    counts = {"subscriptions": 0, "devices": 0,
              "issuances": 0, "skipped": 0, "rows": len(rows)}
    errors = []

    for idx, r in enumerate(rows, start=2):
        has_contract = bool(r["acc_number"] or r["contract_number"])
        has_issuance = not is_placeholder(r["iss_device_type"])

        if not has_contract and not has_issuance:
            counts["skipped"] += 1
            continue

        sub_id = None

        if has_contract:
            amount = parse_amount(r["monthly_amount_raw"])
            vat = round(amount * 0.15, 2) if amount is not None else None
            incl = round(
                amount + vat, 2) if amount is not None and vat is not None else None

            start_date = parse_excel_date(r["start_date_raw"])
            end_date = parse_excel_date(r["end_date_raw"])
            last_used = parse_excel_date(r["last_used_raw"])

            try:
                term_int = int(float(r["contract_term_raw"]))
                contract_term = f"{term_int} Months"
            except (TypeError, ValueError):
                contract_term = r["contract_term_raw"]

            first, last = split_name(r["employee_name"])
            contract_type = infer_contract_type(r["plan_name"])

            try:
                session.execute(
                    text(
                        """
                        INSERT INTO Vodacom_subscription (
                            company_number, account_name, contract_number,
                            Name_, Surname_,
                            Contract_Type, contract_title,
                            Monthly_Costs, VAT, Monthly_Cost_Excl_VAT,
                            Contract_Term, Sim_Card_Number,
                            Inception_Date, Termination_Date, last_used_date,
                            Company
                        ) VALUES (
                            :company_number, :account_name, :contract_number,
                            :name_, :surname_,
                            :contract_type, :contract_title,
                            :monthly_costs, :vat, :monthly_cost_incl_vat,
                            :contract_term, :sim_number,
                            :start_date, :end_date, :last_used_date,
                            :company
                        )
                        """
                    ),
                    {
                        "company_number": r["acc_number"],
                        "account_name": r["account_name"],
                        "contract_number": r["contract_number"],
                        "name_": first,
                        "surname_": last,
                        "contract_type": contract_type,
                        "contract_title": r["plan_name"],
                        "monthly_costs": amount,
                        "vat": vat,
                        "monthly_cost_incl_vat": incl,
                        "contract_term": contract_term,
                        "sim_number": r["sim_number"],
                        "start_date": start_date,
                        "end_date": end_date,
                        "last_used_date": last_used,
                        "company": r["account_name"],
                    },
                )
                sub_id = session.execute(
                    text("SELECT LAST_INSERT_ID()")).scalar_one()
                counts["subscriptions"] += 1
            except Exception as exc:
                errors.append(f"Row {idx}: subscription insert failed - {exc}")
                counts["skipped"] += 1
                continue

            if not is_placeholder(r["cur_device_type"]):
                try:
                    session.execute(
                        text(
                            """
                            INSERT INTO devices (
                                vd_id, Name_, Surname_, Personnel_nr,
                                Company, Client_Division,
                                Device_Name, device_make, device_model,
                                Serial_Number, Device_Description, insurance
                            ) VALUES (
                                :vd_id, :name_, :surname_, :personnel_nr,
                                :company, :client_division,
                                :device_name, :device_make, :device_model,
                                :serial_number, :device_description, :insurance
                            )
                            """
                        ),
                        {
                            "vd_id": sub_id,
                            "name_": first,
                            "surname_": last,
                            "personnel_nr": "",
                            "company": r["account_name"],
                            "client_division": "",
                            "device_name": r["cur_device_type"],
                            "device_make": r["cur_device_make"],
                            "device_model": r["cur_device_model"],
                            "serial_number": r["cur_device_serial"],
                            "device_description": normalize_device_description(r["cur_device_type"]),
                            "insurance": "Unknown",
                        },
                    )
                    counts["devices"] += 1
                except Exception as exc:
                    errors.append(
                        f"Row {idx}: current device insert failed - {exc}")

        if has_issuance:
            iss_date = parse_excel_date(r["iss_date_raw"])
            try:
                session.execute(
                    text(
                        """
                        INSERT INTO device_issuances (
                            vd_id, device_type, device_make,
                            device_model, serial_number, issue_date
                        ) VALUES (
                            :vd_id, :device_type, :device_make,
                            :device_model, :serial_number, :issue_date
                        )
                        """
                    ),
                    {
                        "vd_id": sub_id,
                        "device_type": r["iss_device_type"],
                        "device_make": r["iss_device_make"],
                        "device_model": r["iss_device_model"],
                        "serial_number": r["iss_device_serial"],
                        "issue_date": iss_date,
                    },
                )
                counts["issuances"] += 1
            except Exception as exc:
                errors.append(f"Row {idx}: issuance insert failed - {exc}")

    if errors:
        # keep partial import but return first errors for visibility
        return {"counts": counts, "errors": errors}

    return {"counts": counts, "errors": []}
