"""
SS Net ISP — FastAPI Backend (runs embedded inside the desktop app)
All REST endpoints consumed by the PyQt6 WebEngine frontend.
"""
import calendar
import csv
import io
import os
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import (
    get_db, init_db, log_activity,
    Area, Package, Customer, Bill, ExtraCharge, Payment,
    ReminderLog, ActivityLog, ISPSettings,
)

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

# ─── APP ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="SS Net ISP API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


# ─── PYDANTIC SCHEMAS ─────────────────────────────────────────────────────────
class AreaIn(BaseModel):
    name: str

class PackageIn(BaseModel):
    name: str
    speed: str = ""
    monthly_fee: float = 0.0
    description: str = ""
    is_active: bool = True

class CustomerIn(BaseModel):
    sr_no: str = ""
    username: str
    full_name: str
    mobile: str = ""
    expiring: Optional[str] = None
    package_id: Optional[str] = None
    area_id: Optional[str] = None
    service2: str = ""
    service3: str = ""
    service4: str = ""
    status: str = "active"
    notes: str = ""

class BillIn(BaseModel):
    customer_id: str
    month: str
    package_fee: float = 0.0
    due_date: Optional[str] = None
    notes: str = ""

class GenerateBillsIn(BaseModel):
    month: str
    due_day: int = 28

class PaymentIn(BaseModel):
    bill_id: str
    amount: float
    method: str = "Cash"
    reference: str = ""
    payment_date: Optional[str] = None
    notes: str = ""

class ChargeIn(BaseModel):
    bill_id: str
    charge_type: str = "Other"
    description: str = ""
    amount: float
    charge_date: Optional[str] = None

class ReminderIn(BaseModel):
    customer_id: str
    channel: str = "whatsapp"
    message: str = ""
    bill_id: Optional[str] = None

class SettingsIn(BaseModel):
    isp_name: str = ""
    isp_contact: str = ""
    isp_address: str = ""
    reminder_days: int = 7
    reminder_template: str = ""
    jazzcash_account: str = ""
    easypaisa_account: str = ""
    bank_account: str = ""
    bank_name: str = ""


# ─── DASHBOARD ────────────────────────────────────────────────────────────────
@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    today_ = date.today()
    month  = today_.strftime("%Y-%m")

    total_customers = db.query(Customer).filter(Customer.status == "active").count()
    month_bills = db.query(Bill).filter(Bill.month == month).all()
    paid_bills  = [b for b in month_bills if b.status == "paid"]
    unpaid_bills = db.query(Bill).filter(Bill.status.in_(["unpaid", "partial"])).all()
    overdue_bills = [b for b in unpaid_bills if b.is_overdue()]

    unpaid_charges = db.query(ExtraCharge).filter(ExtraCharge.status == "unpaid").all()

    areas = db.query(Area).all()
    packages = db.query(Package).all()
    recent = db.query(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(15).all()

    return {
        "total_customers": total_customers,
        "paid_this_month": len(paid_bills),
        "paid_amount":     sum(b.amount_paid for b in paid_bills),
        "unpaid_count":    len(unpaid_bills),
        "unpaid_amount":   sum(b.total_amount for b in unpaid_bills),
        "overdue_count":   len(overdue_bills),
        "overdue_bills":   [{"bill_id": b.id, "customer_name": b.customer.full_name,
                              "customer_id": b.customer_id, "mobile": b.customer.mobile,
                              "month": b.month, "total_amount": b.total_amount,
                              "due_date": str(b.due_date) if b.due_date else ""
                             } for b in overdue_bills[:20]],
        "extras_count":   len(unpaid_charges),
        "extras_amount":  sum(c.amount for c in unpaid_charges),
        "areas":     [{"name": a.name, "ccount": len(a.customers)} for a in areas],
        "packages":  [{"name": p.name, "ccount": len(p.customers)} for p in packages],
        "recent_activity": [a.to_dict() for a in recent],
        "current_month": month,
    }


# ─── AREAS ────────────────────────────────────────────────────────────────────
@app.get("/api/areas")
def list_areas(db: Session = Depends(get_db)):
    return [a.to_dict() for a in db.query(Area).order_by(Area.name).all()]

@app.post("/api/areas")
def create_area(data: AreaIn, db: Session = Depends(get_db)):
    if db.query(Area).filter(Area.name == data.name).first():
        raise HTTPException(400, "Area already exists")
    a = Area(name=data.name.strip())
    db.add(a); db.commit(); db.refresh(a)
    log_activity(db, f"Area '{a.name}' created", "system")
    return a.to_dict()

@app.put("/api/areas/{area_id}")
def update_area(area_id: str, data: AreaIn, db: Session = Depends(get_db)):
    a = db.query(Area).get(area_id)
    if not a: raise HTTPException(404, "Not found")
    a.name = data.name.strip(); db.commit(); db.refresh(a)
    return a.to_dict()

@app.delete("/api/areas/{area_id}")
def delete_area(area_id: str, db: Session = Depends(get_db)):
    a = db.query(Area).get(area_id)
    if not a: raise HTTPException(404, "Not found")
    db.delete(a); db.commit()
    return {"ok": True}


# ─── PACKAGES ─────────────────────────────────────────────────────────────────
@app.get("/api/packages")
def list_packages(db: Session = Depends(get_db)):
    return [p.to_dict() for p in db.query(Package).order_by(Package.monthly_fee).all()]

@app.post("/api/packages")
def create_package(data: PackageIn, db: Session = Depends(get_db)):
    p = Package(**data.model_dump())
    db.add(p); db.commit(); db.refresh(p)
    log_activity(db, f"Package '{p.name}' created", "system")
    return p.to_dict()

@app.put("/api/packages/{pkg_id}")
def update_package(pkg_id: str, data: PackageIn, db: Session = Depends(get_db)):
    p = db.query(Package).get(pkg_id)
    if not p: raise HTTPException(404, "Not found")
    for k, v in data.model_dump().items(): setattr(p, k, v)
    db.commit(); db.refresh(p)
    return p.to_dict()

@app.delete("/api/packages/{pkg_id}")
def delete_package(pkg_id: str, db: Session = Depends(get_db)):
    p = db.query(Package).get(pkg_id)
    if not p: raise HTTPException(404, "Not found")
    db.delete(p); db.commit()
    return {"ok": True}


# ─── CUSTOMERS ────────────────────────────────────────────────────────────────
@app.get("/api/customers")
def list_customers(
    search: str = "",
    area_name: str = "",
    status: str = "",
    has_dues: str = "",
    # ── NEW FILTERS ──────────────────────────────────────────────────────────
    bill_status: str = "",   # "paid" | "unpaid" | "partial" | "overdue"
    area_id: str = "",       # filter by area UUID directly
    month: str = "",         # which month to evaluate bill_status against
    # ─────────────────────────────────────────────────────────────────────────
    db: Session = Depends(get_db),
):
    """
    List customers with optional filters:

    search      – full_name / username / mobile (case-insensitive contains)
    area_name   – exact area name string
    area_id     – exact area UUID
    status      – customer account status: active / suspended / disconnected
    has_dues    – "1" → only customers with at least one unpaid/partial bill
    bill_status – filter by current-month bill payment state:
                    "paid"    → customer has a paid bill this month
                    "unpaid"  → customer has an unpaid bill this month
                    "partial" → customer has a partially-paid bill this month
                    "overdue" → customer has an overdue bill (any month)
                    "no_bill" → customer has NO bill generated this month
    month       – YYYY-MM to scope bill_status check (defaults to current month)
    """
    q = db.query(Customer)

    # ── text search ──────────────────────────────────────────────────────────
    if search:
        like = f"%{search}%"
        q = q.filter(
            Customer.full_name.ilike(like) |
            Customer.username.ilike(like) |
            Customer.mobile.ilike(like)
        )

    # ── area filters (name takes priority over id) ───────────────────────────
    if area_name:
        q = q.join(Area, Customer.area_id == Area.id).filter(Area.name == area_name)
    elif area_id:
        q = q.filter(Customer.area_id == area_id)

    # ── account status ───────────────────────────────────────────────────────
    if status:
        q = q.filter(Customer.status == status)

    customers = q.order_by(Customer.full_name).all()

    # ── has_dues (any unpaid/partial bill ever) ───────────────────────────────
    if has_dues == "1":
        customers = [c for c in customers if c.has_dues()]

    # ── bill_status filter ────────────────────────────────────────────────────
    if bill_status:
        target_month = month if month else date.today().strftime("%Y-%m")

        if bill_status == "paid":
            # Customer has a PAID bill in the target month
            customers = [
                c for c in customers
                if any(b.month == target_month and b.status == "paid" for b in c.bills)
            ]

        elif bill_status == "unpaid":
            # Customer has an UNPAID bill in the target month
            customers = [
                c for c in customers
                if any(b.month == target_month and b.status == "unpaid" for b in c.bills)
            ]

        elif bill_status == "partial":
            # Customer has a PARTIALLY-PAID bill in the target month
            customers = [
                c for c in customers
                if any(b.month == target_month and b.status == "partial" for b in c.bills)
            ]

        elif bill_status == "overdue":
            # Customer has at least one overdue bill (any month)
            customers = [
                c for c in customers
                if any(b.is_overdue() for b in c.bills)
            ]

        elif bill_status == "no_bill":
            # Customer has NO bill generated for the target month at all
            customers = [
                c for c in customers
                if not any(b.month == target_month for b in c.bills)
            ]

    return [c.to_dict() for c in customers]


@app.get("/api/customers/{cust_id}")
def get_customer(cust_id: str, db: Session = Depends(get_db)):
    c = db.query(Customer).get(cust_id)
    if not c: raise HTTPException(404, "Not found")
    d = c.to_dict()
    d["bills"] = [b.to_dict() for b in sorted(c.bills, key=lambda x: x.month, reverse=True)]
    return d

@app.post("/api/customers")
def create_customer(data: CustomerIn, db: Session = Depends(get_db)):
    if db.query(Customer).filter(Customer.username == data.username).first():
        raise HTTPException(400, "Username already exists")
    exp = None
    if data.expiring:
        try: exp = date.fromisoformat(data.expiring)
        except: pass
    c = Customer(**{k: v for k, v in data.model_dump().items() if k != "expiring"}, expiring=exp)
    db.add(c); db.commit(); db.refresh(c)
    log_activity(db, f"Customer '{c.full_name}' created", "customer")
    return c.to_dict()

@app.put("/api/customers/{cust_id}")
def update_customer(cust_id: str, data: CustomerIn, db: Session = Depends(get_db)):
    c = db.query(Customer).get(cust_id)
    if not c: raise HTTPException(404, "Not found")
    exp = None
    if data.expiring:
        try: exp = date.fromisoformat(data.expiring)
        except: pass
    for k, v in data.model_dump().items():
        if k == "expiring": setattr(c, k, exp)
        else: setattr(c, k, v)
    db.commit(); db.refresh(c)
    return c.to_dict()

@app.delete("/api/customers/{cust_id}")
def delete_customer(cust_id: str, db: Session = Depends(get_db)):
    c = db.query(Customer).get(cust_id)
    if not c: raise HTTPException(404, "Not found")
    db.delete(c); db.commit()
    return {"ok": True}


# ─── CSV IMPORT ───────────────────────────────────────────────────────────────
@app.post("/api/import-csv")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader  = csv.DictReader(io.StringIO(content))
    added = updated = skipped = 0
    errors = []

    def norm(d):
        return {k.strip().lower(): (v.strip() if v else "") for k, v in d.items() if k}

    for i, raw in enumerate(reader, start=2):
        row = norm(raw)
        username = row.get("username", "").strip()
        if not username:
            skipped += 1; continue

        full_name    = row.get("full name") or row.get("fullname") or row.get("name") or username
        mobile       = row.get("mobile") or row.get("phone") or ""
        expiring_str = row.get("expiring") or row.get("expiry") or ""
        pkg_name     = row.get("package") or ""
        area_name    = row.get("service 1") or row.get("service1") or row.get("area") or "General"
        sr_no        = row.get("sr.no") or row.get("sr no") or row.get("srno") or row.get("sr") or ""
        svc2         = row.get("service 2") or row.get("service2") or ""
        svc3         = row.get("service 3") or row.get("service3") or ""
        svc4         = row.get("service 4") or row.get("service4") or ""

        exp_date = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
            try: exp_date = datetime.strptime(expiring_str, fmt).date(); break
            except: pass

        area_obj = None
        if area_name:
            area_obj = db.query(Area).filter(Area.name == area_name.strip()).first()
            if not area_obj:
                area_obj = Area(name=area_name.strip())
                db.add(area_obj); db.flush()

        pkg_obj = None
        if pkg_name:
            pkg_obj = db.query(Package).filter(Package.name == pkg_name.strip()).first()
            if not pkg_obj:
                pkg_obj = Package(name=pkg_name.strip(), speed=pkg_name.strip())
                db.add(pkg_obj); db.flush()

        try:
            existing = db.query(Customer).filter(Customer.username == username).first()
            if existing:
                existing.sr_no = sr_no; existing.full_name = full_name
                existing.mobile = mobile; existing.expiring = exp_date
                existing.package_id = pkg_obj.id if pkg_obj else None
                existing.package_name_raw = pkg_name
                existing.area_id = area_obj.id if area_obj else None
                existing.area_name_raw = area_name
                existing.service2 = svc2; existing.service3 = svc3; existing.service4 = svc4
                existing.imported_at = datetime.utcnow()
                updated += 1
            else:
                c = Customer(
                    sr_no=sr_no, username=username, full_name=full_name,
                    mobile=mobile, expiring=exp_date,
                    package_id=pkg_obj.id if pkg_obj else None,
                    package_name_raw=pkg_name,
                    area_id=area_obj.id if area_obj else None,
                    area_name_raw=area_name,
                    service2=svc2, service3=svc3, service4=svc4,
                    imported_at=datetime.utcnow(),
                )
                db.add(c); added += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")

    db.commit()
    log_activity(db, f"CSV import: {added} added, {updated} updated", "import")
    return {"added": added, "updated": updated, "skipped": skipped, "errors": errors[:20]}


# ─── BILLS ────────────────────────────────────────────────────────────────────
@app.put("/api/bills/{bill_id}")
def update_bill(bill_id: str, data: BillIn, db: Session = Depends(get_db)):
    bill = db.query(Bill).get(bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    if data.month:
        bill.month = data.month
    if data.package_fee is not None:
        bill.package_fee = data.package_fee
    if data.due_date:
        bill.due_date = date.fromisoformat(data.due_date)
    if data.notes is not None:
        bill.notes = data.notes
    db.commit(); db.refresh(bill)
    return bill.to_dict()

@app.get("/api/bills")
def list_bills(
    search: str = "",
    month: str = "",
    status: str = "",
    area: str = "",
    customer_id: str = "",
    overdue: str = "",
    db: Session = Depends(get_db),
):
    q = db.query(Bill)
    if customer_id: q = q.filter(Bill.customer_id == customer_id)
    if month:       q = q.filter(Bill.month == month)
    if status:      q = q.filter(Bill.status == status)
    if search:
        q = q.join(Customer).filter(
            Customer.full_name.ilike(f"%{search}%") |
            Customer.username.ilike(f"%{search}%")
        )
    if area:
        q = q.join(Customer).join(Area).filter(Area.name == area)
    bills = q.order_by(Bill.month.desc()).all()
    if overdue == "1":
        bills = [b for b in bills if b.is_overdue()]
    return [b.to_dict() for b in bills]

@app.post("/api/bills")
def create_bill(data: BillIn, db: Session = Depends(get_db)):
    existing = db.query(Bill).filter(
        Bill.customer_id == data.customer_id, Bill.month == data.month
    ).first()
    if existing:
        return existing.to_dict()
    due = date.fromisoformat(data.due_date) if data.due_date else None
    b = Bill(customer_id=data.customer_id, month=data.month,
             package_fee=data.package_fee, total_amount=data.package_fee,
             due_date=due, notes=data.notes)
    db.add(b); db.commit(); db.refresh(b)
    return b.to_dict()

@app.post("/api/generate-bills")
def generate_bills(data: GenerateBillsIn, db: Session = Depends(get_db)):
    year, mon = map(int, data.month.split("-"))
    last_day  = calendar.monthrange(year, mon)[1]
    due_date_ = date(year, mon, min(data.due_day, last_day))
    customers = db.query(Customer).filter(Customer.status == "active").all()
    created = skipped = 0
    for c in customers:
        ex = db.query(Bill).filter(Bill.customer_id == c.id, Bill.month == data.month).first()
        if ex:
            skipped += 1; continue
        b = Bill(customer_id=c.id, month=data.month,
                 package_fee=c.package_fee, total_amount=c.package_fee,
                 due_date=due_date_)
        db.add(b); created += 1
    db.commit()
    log_activity(db, f"Generated {created} bills for {data.month}", "bill")
    return {"created": created, "skipped": skipped, "month": data.month}


# ─── PAYMENTS ─────────────────────────────────────────────────────────────────
@app.post("/api/payments")
def add_payment(data: PaymentIn, db: Session = Depends(get_db)):
    bill = db.query(Bill).get(data.bill_id)
    if not bill: raise HTTPException(404, "Bill not found")
    pay_date = date.fromisoformat(data.payment_date) if data.payment_date else date.today()
    p = Payment(bill_id=data.bill_id, amount=data.amount, method=data.method,
                reference=data.reference, payment_date=pay_date, notes=data.notes)
    db.add(p); db.flush()
    bill.recalc(db)
    if bill.status == "paid":
        bill.paid_date   = pay_date
        bill.paid_method = data.method
        bill.paid_ref    = data.reference
        db.commit()
    log_activity(db, f"Payment PKR {data.amount:,.0f} for {bill.customer.full_name} ({bill.month})", "payment")
    return p.to_dict()


# ─── EXTRA CHARGES ────────────────────────────────────────────────────────────
@app.post("/api/charges")
def add_charge(data: ChargeIn, db: Session = Depends(get_db)):
    bill = db.query(Bill).get(data.bill_id)
    if not bill: raise HTTPException(404, "Bill not found")
    chg_date = date.fromisoformat(data.charge_date) if data.charge_date else date.today()
    c = ExtraCharge(bill_id=data.bill_id, charge_type=data.charge_type,
                    description=data.description, amount=data.amount,
                    charge_date=chg_date)
    db.add(c); db.flush()
    bill.recalc(db)
    log_activity(db, f"Charge {data.charge_type} PKR {data.amount:,.0f} added for {bill.customer.full_name}", "charge")
    return c.to_dict()

@app.post("/api/charges/{charge_id}/mark-paid")
def mark_charge_paid(charge_id: str, db: Session = Depends(get_db)):
    c = db.query(ExtraCharge).get(charge_id)
    if not c: raise HTTPException(404)
    c.status = "paid"; c.paid_date = date.today(); db.commit()
    c.bill.recalc(db)
    return {"ok": True}


# ─── REMINDERS ────────────────────────────────────────────────────────────────
@app.post("/api/log-reminder")
def log_reminder(data: ReminderIn, db: Session = Depends(get_db)):
    cust = db.query(Customer).get(data.customer_id)
    if not cust: raise HTTPException(404, "Customer not found")
    r = ReminderLog(customer_id=data.customer_id, channel=data.channel,
                    message=data.message, bill_id=data.bill_id)
    db.add(r); db.commit()
    log_activity(db, f"Reminder sent to {cust.full_name} via {data.channel}", "reminder")
    return {"ok": True}

@app.get("/api/customers-due-soon")
def customers_due_soon(days: int = 7, db: Session = Depends(get_db)):
    today_ = date.today()
    threshold = today_ + timedelta(days=days)
    custs = db.query(Customer).filter(
        Customer.status == "active",
        Customer.expiring != None,
        Customer.expiring >= today_,
        Customer.expiring <= threshold,
    ).order_by(Customer.expiring).all()
    result = []
    for c in custs:
        days_left = (c.expiring - today_).days if c.expiring else None
        result.append({
            "id": c.id, "username": c.username, "full_name": c.full_name,
            "mobile": c.mobile, "mobile_e164": c.get_mobile_e164(),
            "package": c.package_display, "area": c.area_display,
            "expiring": str(c.expiring), "days_left": days_left,
            "unpaid_amt": c.total_due(),
        })
    return result

@app.get("/api/reminders")
def list_reminders(db: Session = Depends(get_db)):
    return [r.to_dict() for r in db.query(ReminderLog).order_by(ReminderLog.sent_at.desc()).limit(100).all()]


# ─── SETTINGS ─────────────────────────────────────────────────────────────────
@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    s = db.query(ISPSettings).get(1)
    if not s:
        s = ISPSettings(id=1); db.add(s); db.commit(); db.refresh(s)
    return s.to_dict()

@app.patch("/api/settings")
def update_settings(data: SettingsIn, db: Session = Depends(get_db)):
    s = db.query(ISPSettings).get(1)
    if not s: s = ISPSettings(id=1); db.add(s)
    for k, v in data.model_dump().items():
        if v: setattr(s, k, v)
    db.commit(); db.refresh(s)
    log_activity(db, "ISP settings updated", "system")
    return s.to_dict()


# ─── QR CODE HELPER ───────────────────────────────────────────────────────────
import qrcode
from reportlab.lib.utils import ImageReader

def _make_qr_image(data_str: str, box_size: int = 6):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M,
                        box_size=box_size, border=2)
    qr.add_data(data_str)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image()

def _qr_flowable(data_str: str, size: float = 3.5*cm):
    from reportlab.platypus import Image as RLImage
    img = _make_qr_image(data_str)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return RLImage(buf, width=size, height=size)

def _payment_info_elems(s, style_h2, style_body):
    elems = []
    lines = []
    if s.jazzcash_account:
        lines.append(f"<b>JazzCash:</b>  {s.jazzcash_account}")
    if s.easypaisa_account:
        lines.append(f"<b>EasyPaisa:</b>  {s.easypaisa_account}")
    if s.bank_account:
        bank = s.bank_account
        if s.bank_name:
            bank += f"  ({s.bank_name})"
        lines.append(f"<b>Bank Transfer:</b>  {bank}")
    if lines:
        elems.append(Paragraph("How to Pay", style_h2))
        elems.append(Spacer(1, 0.15*cm))
        for ln in lines:
            elems.append(Paragraph(ln, style_body))
            elems.append(Spacer(1, 0.08*cm))
        elems.append(Spacer(1, 0.2*cm))
    return elems


# ─── PDF BILL (SINGLE) ────────────────────────────────────────────────────────
@app.get("/api/bills/{bill_id}/pdf")
def generate_pdf(bill_id: str, db: Session = Depends(get_db)):
    bill = db.query(Bill).get(bill_id)
    if not bill: raise HTTPException(404, "Bill not found")
    s    = db.query(ISPSettings).get(1) or ISPSettings()
    cust = bill.customer

    buf  = io.BytesIO()
    doc  = SimpleDocTemplate(buf, pagesize=A4,
                              leftMargin=2*cm, rightMargin=2*cm,
                              topMargin=2*cm, bottomMargin=2*cm)
    st   = getSampleStyleSheet()
    h1   = ParagraphStyle("h1", fontSize=22, fontName="Helvetica-Bold",
                           textColor=colors.HexColor("#0a0e1a"))
    h2   = ParagraphStyle("h2", fontSize=12, fontName="Helvetica-Bold",
                           textColor=colors.HexColor("#00a8cc"))
    rt   = ParagraphStyle("rt", fontSize=10, fontName="Helvetica", alignment=TA_RIGHT)
    body_style = ParagraphStyle("body_pay", fontSize=10, fontName="Helvetica",
                                 textColor=colors.HexColor("#333333"), leading=14)
    stamp_style = ParagraphStyle(
        "stamp", fontSize=38, fontName="Helvetica-Bold",
        textColor=colors.green if bill.status == "paid" else colors.red,
        alignment=TA_CENTER)
    foot = ParagraphStyle("foot", fontSize=8, textColor=colors.grey, alignment=TA_CENTER)

    elems = []
    qr_data = f"BILL:{bill.id[:8].upper()}|AMT:{bill.total_amount:.0f}|STATUS:{bill.status.upper()}"
    qr_img  = _qr_flowable(qr_data, size=2.8*cm)

    hdr = Table([
        [Paragraph(f"<b>{s.isp_name}</b>", h1),
         Paragraph(f"<b>INVOICE</b><br/><font size=9>#{bill.id[:8].upper()}</font>", rt),
         qr_img]
    ], colWidths=[9*cm, 5.5*cm, 3*cm])
    hdr.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"),
                              ("ALIGN", (2,0), (2,0), "RIGHT")]))
    elems += [hdr, HRFlowable(width="100%", thickness=2, color=colors.HexColor("#00a8cc")),
              Spacer(1, 0.3*cm)]

    stamp = "✓  PAID" if bill.status == "paid" else ("⚠  OVERDUE" if bill.is_overdue() else "UNPAID")
    elems += [Paragraph(stamp, stamp_style), Spacer(1, 0.3*cm)]

    info = [
        ["Customer",  cust.full_name,       "Bill Month", bill.month],
        ["Username",  cust.username,         "Due Date",   str(bill.due_date or "—")],
        ["Mobile",    cust.mobile or "—",    "Paid Date",  str(bill.paid_date or "—")],
        ["Area",      cust.area_display,     "Method",     bill.paid_method or "—"],
        ["Package",   cust.package_display,  "Ref No.",    bill.paid_ref or "—"],
        ["Contact",   s.isp_contact,         "Address",    s.isp_address],
    ]
    itbl = Table(info, colWidths=[3*cm, 6*cm, 3*cm, 5*cm])
    itbl.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (0,-1), colors.HexColor("#f0f8ff")),
        ("BACKGROUND",  (2,0), (2,-1), colors.HexColor("#f0f8ff")),
        ("FONTNAME",    (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",    (2,0), (2,-1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, colors.HexColor("#f9f9f9")]),
        ("PADDING",     (0,0), (-1,-1), 6),
    ]))
    elems += [itbl, Spacer(1, 0.4*cm)]

    elems.append(Paragraph("Charges Breakdown", h2))
    elems.append(Spacer(1, 0.15*cm))
    rows = [["#", "Description", "Date", "Status", "Amount"]]
    rows.append(["1", f"Monthly Package — {cust.package_display}",
                 str(bill.created_at.date()), bill.status.upper(), f"PKR {bill.package_fee:,.0f}"])
    for i, ch in enumerate(bill.charges, 2):
        rows.append([str(i), f"{ch.charge_type}" + (f" — {ch.description}" if ch.description else ""),
                     str(ch.charge_date), ch.status.upper(), f"PKR {ch.amount:,.0f}"])
    rows.append(["", "", "", "TOTAL",       f"PKR {bill.total_amount:,.0f}"])
    if bill.amount_paid > 0:
        rows.append(["", "", "", "PAID",    f"PKR {bill.amount_paid:,.0f}"])
        rows.append(["", "", "", "BALANCE", f"PKR {bill.remaining_due():,.0f}"])

    ctbl = Table(rows, colWidths=[0.8*cm, 8*cm, 2.5*cm, 2.5*cm, 3.2*cm])
    ctbl.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0), colors.HexColor("#0a0e1a")),
        ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS", (0,1), (-1,-4), [colors.white, colors.HexColor("#f9f9f9")]),
        ("BACKGROUND",  (0,-1), (-1,-1),
         colors.HexColor("#fff3cd") if bill.remaining_due() > 0 else colors.HexColor("#d4edda")),
        ("FONTNAME",    (3,-1), (-1,-1), "Helvetica-Bold"),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#dddddd")),
        ("PADDING",     (0,0), (-1,-1), 6),
        ("ALIGN",       (4,0), (4,-1), "RIGHT"),
        ("ALIGN",       (3,0), (3,-1), "RIGHT"),
    ]))
    elems += [ctbl, Spacer(1, 0.4*cm)]

    if bill.payments:
        elems.append(Paragraph("Payment History", h2))
        elems.append(Spacer(1, 0.15*cm))
        pr = [["Date", "Method", "Reference", "Amount"]]
        for p in bill.payments:
            pr.append([str(p.payment_date), p.method, p.reference or "—", f"PKR {p.amount:,.0f}"])
        ptbl = Table(pr, colWidths=[3*cm, 3.5*cm, 6*cm, 4.5*cm])
        ptbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#00a8cc")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 9),
            ("GRID",       (0,0), (-1,-1), 0.5, colors.HexColor("#dddddd")),
            ("PADDING",    (0,0), (-1,-1), 6),
            ("ALIGN",      (3,0), (3,-1), "RIGHT"),
        ]))
        elems.append(ptbl)

    elems += [Spacer(1, 0.3*cm)]
    elems += _payment_info_elems(s, h2, body_style)
    elems += [Spacer(1, 0.3*cm),
              HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")),
              Spacer(1, 0.2*cm),
              Paragraph(f"Thank you for using {s.isp_name}. "
                        f"Contact: {s.isp_contact}  |  {s.isp_address}", foot)]
    doc.build(elems)
    buf.seek(0)
    fname = f"Bill_{cust.username}_{bill.month}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'inline; filename="{fname}"'})


# ─── PDF CUSTOMER CONSOLIDATED ────────────────────────────────────────────────
@app.get("/api/customers/{cust_id}/bill-pdf")
def generate_customer_bill_pdf(cust_id: str, db: Session = Depends(get_db)):
    cust = db.query(Customer).get(cust_id)
    if not cust: raise HTTPException(404, "Customer not found")
    s = db.query(ISPSettings).get(1) or ISPSettings()

    unpaid_bills = [b for b in cust.bills if b.status in ("unpaid", "partial")]
    unpaid_bills.sort(key=lambda b: b.month, reverse=True)

    if not unpaid_bills:
        raise HTTPException(404, "No unpaid bills for this customer")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    h1 = ParagraphStyle("ch1", fontSize=20, fontName="Helvetica-Bold",
                         textColor=colors.HexColor("#0a0e1a"))
    h2 = ParagraphStyle("ch2", fontSize=12, fontName="Helvetica-Bold",
                         textColor=colors.HexColor("#00a8cc"))
    h3 = ParagraphStyle("ch3", fontSize=10, fontName="Helvetica-Bold",
                         textColor=colors.HexColor("#333333"))
    rt = ParagraphStyle("crt", fontSize=10, fontName="Helvetica", alignment=TA_RIGHT)
    body = ParagraphStyle("cbody", fontSize=10, fontName="Helvetica",
                           textColor=colors.HexColor("#333333"), leading=14)
    stamp_red = ParagraphStyle("cstamp_red", fontSize=16, fontName="Helvetica-Bold",
                                textColor=colors.red, alignment=TA_CENTER)
    foot = ParagraphStyle("cfoot", fontSize=8, textColor=colors.grey, alignment=TA_CENTER)

    elems = []

    hdr = Table([[Paragraph(f"<b>{s.isp_name}</b>", h1),
                  Paragraph(f"<b>CUSTOMER BILL STATEMENT</b>", rt)]],
                colWidths=[10*cm, 7*cm])
    hdr.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    elems += [hdr, HRFlowable(width="100%", thickness=2, color=colors.HexColor("#00a8cc")),
              Spacer(1, 0.3*cm)]

    total_due = sum(b.remaining_due() for b in unpaid_bills)
    cinfo = [
        ["Customer",  cust.full_name,       "Total Unpaid Bills", str(len(unpaid_bills))],
        ["Username",  cust.username,         "Total Due Amount",   f"PKR {total_due:,.0f}"],
        ["Mobile",    cust.mobile or "—",    "Package",            cust.package_display],
        ["Area",      cust.area_display,     "ISP Contact",        s.isp_contact],
    ]
    citbl = Table(cinfo, colWidths=[3*cm, 6*cm, 4*cm, 4*cm])
    citbl.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (0,-1), colors.HexColor("#f0f8ff")),
        ("BACKGROUND",  (2,0), (2,-1), colors.HexColor("#f0f8ff")),
        ("FONTNAME",    (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",    (2,0), (2,-1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
        ("PADDING",     (0,0), (-1,-1), 6),
    ]))
    elems += [citbl, Spacer(1, 0.4*cm)]
    elems += [Paragraph(f"⚠  TOTAL DUE: PKR {total_due:,.0f}", stamp_red), Spacer(1, 0.4*cm)]

    elems.append(Paragraph("Unpaid Bills Detail", h2))
    elems.append(Spacer(1, 0.15*cm))

    bill_rows = [["Month", "Due Date", "Total", "Paid", "Remaining", "Status", "QR Code"]]
    for b in unpaid_bills:
        qr_data = f"BILL:{b.id[:8].upper()}|AMT:{b.remaining_due():.0f}|STATUS:{b.status.upper()}"
        qr = _qr_flowable(qr_data, size=1.8*cm)
        status_text = "OVERDUE" if b.is_overdue() else b.status.upper()
        bill_rows.append([
            b.month, str(b.due_date or "—"),
            f"PKR {b.total_amount:,.0f}",
            f"PKR {b.amount_paid:,.0f}" if b.amount_paid > 0 else "—",
            f"PKR {b.remaining_due():,.0f}",
            status_text, qr,
        ])

    btbl = Table(bill_rows, colWidths=[2*cm, 2.2*cm, 2.5*cm, 2.2*cm, 2.5*cm, 2*cm, 2.2*cm])
    btbl_style = [
        ("BACKGROUND",  (0,0), (-1,0), colors.HexColor("#0a0e1a")),
        ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#dddddd")),
        ("PADDING",     (0,0), (-1,-1), 5),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",       (2,0), (4,-1), "RIGHT"),
    ]
    for ri in range(1, len(bill_rows)):
        b = unpaid_bills[ri - 1]
        if b.is_overdue():
            btbl_style.append(("BACKGROUND", (0, ri), (-1, ri), colors.HexColor("#fff0f0")))
        elif ri % 2 == 0:
            btbl_style.append(("BACKGROUND", (0, ri), (-1, ri), colors.HexColor("#f9f9f9")))
    btbl.setStyle(TableStyle(btbl_style))
    elems += [btbl, Spacer(1, 0.3*cm)]

    for b in unpaid_bills:
        if b.charges:
            elems.append(Paragraph(f"Extra Charges — {b.month}", h3))
            elems.append(Spacer(1, 0.1*cm))
            ch_rows = [["Type", "Description", "Amount"]]
            for ch in b.charges:
                ch_rows.append([ch.charge_type, ch.description or "—", f"PKR {ch.amount:,.0f}"])
            chtbl = Table(ch_rows, colWidths=[3.5*cm, 9*cm, 4*cm])
            chtbl.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e8f4f8")),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 8.5),
                ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#dddddd")),
                ("PADDING",    (0,0), (-1,-1), 4),
                ("ALIGN",      (2,0), (2,-1), "RIGHT"),
            ]))
            elems += [chtbl, Spacer(1, 0.2*cm)]

    elems.append(Spacer(1, 0.2*cm))
    summary = Table([["", "", "Grand Total Due:", f"PKR {total_due:,.0f}"]],
                    colWidths=[5*cm, 5*cm, 3.5*cm, 3.5*cm])
    summary.setStyle(TableStyle([
        ("BACKGROUND",  (2,0), (-1,0), colors.HexColor("#fff3cd")),
        ("FONTNAME",    (2,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 11),
        ("ALIGN",       (3,0), (3,0), "RIGHT"),
        ("GRID",        (2,0), (-1,0), 1, colors.HexColor("#cccccc")),
        ("PADDING",     (0,0), (-1,-1), 8),
    ]))
    elems += [summary, Spacer(1, 0.4*cm)]
    elems += _payment_info_elems(s, h2, body)
    elems += [Spacer(1, 0.3*cm),
              HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")),
              Spacer(1, 0.2*cm),
              Paragraph(f"Thank you for using {s.isp_name}. "
                        f"Contact: {s.isp_contact}  |  {s.isp_address}", foot),
              Spacer(1, 0.1*cm),
              Paragraph(f"Generated on {date.today().strftime('%d %b %Y')}", foot)]

    doc.build(elems)
    buf.seek(0)
    fname = f"Bills_{cust.username}_unpaid.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'inline; filename="{fname}"'})


# ─── STATIC FILES & INDEX ─────────────────────────────────────────────────────
import os as _os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_STATIC = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "static")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

@app.get("/")
def serve_index():
    return FileResponse(_os.path.join(_STATIC, "index.html"))
