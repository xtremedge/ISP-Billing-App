"""
SS Net ISP — FastAPI Backend
SS Net ISP — FastAPI Backend (runs embedded inside the desktop app)
All REST endpoints consumed by the PyQt6 WebEngine frontend.
"""
import calendar
import csv
import hashlib
import hmac
import io
import os
import secrets
import shutil
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import (
    get_db, init_db, log_activity, DB_PATH, ENGINE, SessionLocal,
    Area, Package, Customer, Bill, ExtraCharge, Payment,
    ReminderLog, ActivityLog, ISPSettings, AdminUser,
)

# PDF / ReportLab
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm, mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, HRFlowable, KeepTogether)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.platypus import Image as RLImage
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie

# QR code
import qrcode

# ─── APP ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="SS Net ISP API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

_AUTH_TTL_SECONDS = 60 * 60 * 24
_AUTH_TOKENS = {}
_AUTH_PUBLIC_PATHS = {
    "/api/auth/status",
    "/api/auth/signup",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/recover-username",
    "/api/auth/reset-password",
}


def _normalize_username(value: str) -> str:
    return (value or "").strip().lower()


def _new_salt() -> str:
    return secrets.token_hex(16)


def _hash_secret(value: str, salt: str) -> str:
    raw = (value or "").encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", raw, salt.encode("utf-8"), 260000).hex()


def _verify_secret(value: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(_hash_secret(value, salt), expected_hash or "")


def _is_strong_password(password: str) -> bool:
    pwd = password or ""
    if len(pwd) < 8:
        return False
    has_upper = any(ch.isupper() for ch in pwd)
    has_lower = any(ch.islower() for ch in pwd)
    has_digit = any(ch.isdigit() for ch in pwd)
    return has_upper and has_lower and has_digit


def _cleanup_tokens() -> None:
    now = datetime.utcnow()
    expired = [token for token, info in _AUTH_TOKENS.items() if info["expires_at"] <= now]
    for token in expired:
        _AUTH_TOKENS.pop(token, None)


def _issue_auth_token(username: str) -> str:
    _cleanup_tokens()
    token = secrets.token_urlsafe(32)
    _AUTH_TOKENS[token] = {
        "username": username,
        "expires_at": datetime.utcnow() + timedelta(seconds=_AUTH_TTL_SECONDS),
    }
    return token


def _get_token_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return (request.query_params.get("auth_token") or "").strip()


def _validate_auth_token(token: str) -> str:
    if not token:
        return ""
    _cleanup_tokens()
    row = _AUTH_TOKENS.get(token)
    if not row:
        return ""
    if row["expires_at"] <= datetime.utcnow():
        _AUTH_TOKENS.pop(token, None)
        return ""
    return row["username"]


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api"):
        return await call_next(request)
    if path in _AUTH_PUBLIC_PATHS:
        return await call_next(request)

    db = SessionLocal()
    try:
        admin_exists = bool(db.query(AdminUser).first())
    finally:
        db.close()

    if not admin_exists:
        return JSONResponse(
            status_code=401,
            content={"detail": "Admin setup required. Please create admin account first."},
        )

    username = _validate_auth_token(_get_token_from_request(request))
    if not username:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized. Please login."})

    request.state.admin_username = username
    return await call_next(request)

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
    status: Optional[str] = None

class BillUpdateIn(BaseModel):
    month: Optional[str] = None
    package_fee: Optional[float] = None
    due_date: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None

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
    isp_email: str = ""
    isp_website: str = ""
    isp_city: str = ""
    reminder_days: int = 7
    reminder_template: str = ""
    jazzcash_account: str = ""
    easypaisa_account: str = ""
    bank_account: str = ""
    bank_name: str = ""
    theme_bg: str = "#0a0e1a"
    theme_surface: str = "#111827"
    theme_surface2: str = "#1a2236"
    theme_border: str = "#1e2d45"
    theme_accent: str = "#00d4ff"
    theme_accent2: str = "#ff6b35"


class AdminSignupIn(BaseModel):
    username: str
    password: str
    recovery_key: str


class AdminLoginIn(BaseModel):
    username: str
    password: str


class RecoverUsernameIn(BaseModel):
    recovery_key: str


class ResetPasswordIn(BaseModel):
    username: str
    recovery_key: str
    new_password: str


# ── Export-as-PDF request body ─────────────────────────────────────────────
class ExportCustomersIn(BaseModel):
    search: str = ""
    area_name: str = ""
    bill_status: str = ""
    month: str = ""
    status: str = ""
    title: str = "Customer Report"


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/auth/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).first()
    username = _validate_auth_token(_get_token_from_request(request))
    authenticated = bool(admin and username)
    return {
        "setup_required": admin is None,
        "authenticated": authenticated,
        "username": username if authenticated else "",
    }


@app.post("/api/auth/signup")
def auth_signup(data: AdminSignupIn, db: Session = Depends(get_db)):
    if db.query(AdminUser).first():
        raise HTTPException(400, "Admin account already exists. Please login.")

    username = _normalize_username(data.username)
    if len(username) < 4:
        raise HTTPException(400, "Username must be at least 4 characters.")
    if not _is_strong_password(data.password):
        raise HTTPException(400, "Password must be 8+ chars with upper, lower, and number.")

    recovery_key = (data.recovery_key or "").strip()
    if len(recovery_key) < 6:
        raise HTTPException(400, "Recovery key must be at least 6 characters.")

    pass_salt = _new_salt()
    recovery_salt = _new_salt()
    admin = AdminUser(
        id=1,
        username=username,
        password_hash=_hash_secret(data.password, pass_salt),
        password_salt=pass_salt,
        recovery_key_hash=_hash_secret(recovery_key, recovery_salt),
        recovery_key_salt=recovery_salt,
    )
    db.add(admin)
    db.commit()
    log_activity(db, "Admin account created", "system")
    return {"ok": True, "message": "Admin account created. Please login."}


@app.post("/api/auth/login")
def auth_login(data: AdminLoginIn, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).first()
    if not admin:
        raise HTTPException(400, "Admin setup required. Please sign up first.")

    username = _normalize_username(data.username)
    if username != _normalize_username(admin.username):
        raise HTTPException(401, "Invalid username or password.")

    if not _verify_secret(data.password, admin.password_salt, admin.password_hash):
        raise HTTPException(401, "Invalid username or password.")

    token = _issue_auth_token(admin.username)
    log_activity(db, f"Admin login: {admin.username}", "system")
    return {
        "ok": True,
        "token": token,
        "username": admin.username,
        "expires_in": _AUTH_TTL_SECONDS,
    }


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    token = _get_token_from_request(request)
    if token:
        _AUTH_TOKENS.pop(token, None)
    return {"ok": True}


@app.post("/api/auth/recover-username")
def recover_username(data: RecoverUsernameIn, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).first()
    if not admin:
        raise HTTPException(400, "Admin setup required. Please sign up first.")

    key = (data.recovery_key or "").strip()
    if not _verify_secret(key, admin.recovery_key_salt, admin.recovery_key_hash):
        raise HTTPException(401, "Invalid recovery key.")

    return {"ok": True, "username": admin.username}


@app.post("/api/auth/reset-password")
def reset_password(data: ResetPasswordIn, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).first()
    if not admin:
        raise HTTPException(400, "Admin setup required. Please sign up first.")

    if _normalize_username(data.username) != _normalize_username(admin.username):
        raise HTTPException(401, "Invalid recovery request.")

    key = (data.recovery_key or "").strip()
    if not _verify_secret(key, admin.recovery_key_salt, admin.recovery_key_hash):
        raise HTTPException(401, "Invalid recovery request.")

    if not _is_strong_password(data.new_password):
        raise HTTPException(400, "New password must be 8+ chars with upper, lower, and number.")

    new_salt = _new_salt()
    admin.password_salt = new_salt
    admin.password_hash = _hash_secret(data.new_password, new_salt)
    db.commit()
    _AUTH_TOKENS.clear()
    log_activity(db, "Admin password reset", "system")
    return {"ok": True, "message": "Password reset successful. Please login again."}


# ═══════════════════════════════════════════════════════════════════════════════
#  PDF HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_qr_buf(data_str: str, box_size: int = 5) -> io.BytesIO:
    qr = qrcode.QRCode(version=1,
                        error_correction=qrcode.constants.ERROR_CORRECT_M,
                        box_size=box_size, border=2)
    qr.add_data(data_str)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").get_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _qr_img(data_str: str, size: float = 2.4 * cm) -> RLImage:
    return RLImage(_make_qr_buf(data_str), width=size, height=size)


def _logo_path() -> str:
    """Preferred logo path in user data dir; fallback to bundled static path."""
    user_logo = os.path.join(os.path.dirname(DB_PATH), "logo.png")
    if os.path.exists(user_logo):
        return user_logo
    bundled_logo = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "logo.png")
    return bundled_logo


def _logo_img(s: ISPSettings, width: float = 2.6 * cm) -> Optional[RLImage]:
    """Return a logo RLImage if a logo file exists, else None."""
    logo_path = _logo_path()
    if os.path.exists(logo_path):
        from PIL import Image as PILImage
        pil = PILImage.open(logo_path)
        w, h = pil.size
        ratio = h / w
        return RLImage(logo_path, width=width, height=width * ratio)
    return None


def _isp_info_lines(s: ISPSettings) -> list:
    """Compact contact lines for header/footer in sample-style ordering."""
    lines = []
    if s.isp_address:
        lines.append(s.isp_address)
    contact_bits = []
    if s.isp_email:
        contact_bits.append(s.isp_email)
    if s.isp_contact:
        contact_bits.append(s.isp_contact)
    if s.isp_website:
        contact_bits.append(s.isp_website)
    if contact_bits:
        lines.append(" | ".join(contact_bits))
    return lines


def _payment_section(s: ISPSettings) -> list:
    lines = []
    if s.jazzcash_account:
        lines.append(f"JazzCash: {s.jazzcash_account}")
    if s.easypaisa_account:
        lines.append(f"EasyPaisa: {s.easypaisa_account}")
    if s.bank_account:
        bank = s.bank_account
        if s.bank_name:
            bank += f"  ({s.bank_name})"
        lines.append(f"Bank: {bank}")
    return lines


def _fmt_date_short(d: Optional[date]) -> str:
    if not d:
        return "—"
    return d.strftime("%d-%b-%y")


def _fmt_month_label(month_key: str) -> str:
    try:
        dt = datetime.strptime(month_key, "%Y-%m")
        return dt.strftime("%b %Y")
    except Exception:
        return month_key or "—"


def _invoice_no(bill: Bill) -> str:
    raw = (bill.id or "").replace("-", "")
    seed = int(raw[:8], 16) if raw else 0
    serial = str(seed % 10000).zfill(4)
    return f"INV-{bill.month.replace('-', '')}-{serial}"


# ═══════════════════════════════════════════════════════════════════════════════
#  PROFESSIONAL BILL PDF  (matches National Broadband layout)
# ═══════════════════════════════════════════════════════════════════════════════

def build_bill_pdf(bill: Bill, s: ISPSettings) -> io.BytesIO:
    """
    Layout (top → bottom):
    ┌──────────────────────────────────────────────────┐
    │  [LOGO]          COMPANY NAME  ┆  contact box    │
    ├──────────────────────────────────────────────────┤
    │  BILL TO (left)      Invoice details (right)     │
    ├──────────────────────────────────────────────────┤
    │  CHARGES (left)      PAYMENT HISTORY (right)     │
    ├──────────────────────────────────────────────────┤
    │  Net Payable: PKR X,XXX                          │
    ├──────────────────────────────────────────────────┤
    │  ─── QR Code ─────────────────────────── copy ── │
    ├──────────────────────────────────────────────────┤
    │  CUSTOMER/CONNECTION INFO   ┆  CHARGES DETAIL    │
    └──────────────────────────────────────────────────┘
    """
    cust = bill.customer
    buf  = io.BytesIO()
    doc  = SimpleDocTemplate(buf, pagesize=A4,
                              leftMargin=1.5*cm, rightMargin=1.5*cm,
                              topMargin=1.2*cm, bottomMargin=1.2*cm)

    # ── Styles ────────────────────────────────────────────────────────────────
    DARK  = colors.HexColor(s.theme_surface or "#0d1b2e")
    CYAN  = colors.HexColor(s.theme_accent or "#00a8cc")
    LGRAY = colors.HexColor("#f5f7fa")
    MGRAY = colors.HexColor("#e2e8f0")
    DTEXT = colors.HexColor("#2d3748")
    RED   = colors.red
    GRN   = colors.HexColor("#1a9e5f")

    def sty(name, **kw):
        base = dict(fontName="Helvetica", fontSize=9, textColor=DTEXT,
                    leading=13, spaceBefore=0, spaceAfter=0)
        base.update(kw)
        return ParagraphStyle(name, **base)

    S_ISP_NAME  = sty("isn",  fontSize=15, fontName="Helvetica-Bold",
                       textColor=DARK, alignment=TA_RIGHT)
    S_ISP_SUB   = sty("isub", fontSize=8,  textColor=colors.HexColor("#555"),
                       alignment=TA_RIGHT, leading=11)
    S_SEC_HDR   = sty("shdr", fontSize=7.5, fontName="Helvetica-Bold",
                       textColor=colors.white, alignment=TA_LEFT)
    S_LBL       = sty("lbl",  fontSize=8.2, fontName="Helvetica-Bold",
                       textColor=DTEXT)
    S_VAL       = sty("val",  fontSize=8.2, textColor=DTEXT)
    S_AMT_BIG   = sty("abig", fontSize=14, fontName="Helvetica-Bold",
                       textColor=DARK)
    S_AMT_LBL   = sty("albl", fontSize=9,  fontName="Helvetica-Bold",
                       textColor=DTEXT)
    S_FOOT      = sty("ft",   fontSize=7.5, textColor=colors.HexColor("#777"),
                       alignment=TA_CENTER)
    S_COPY      = sty("cp",   fontSize=7.5, textColor=colors.HexColor("#999"),
                       alignment=TA_RIGHT)
    S_WARN      = sty("wn",   fontSize=8,  textColor=colors.HexColor("#555"),
                       alignment=TA_LEFT)
    S_STAMP     = sty("stmp", fontSize=22, fontName="Helvetica-Bold",
                       textColor=GRN if bill.status == "paid" else RED,
                       alignment=TA_RIGHT)

    logo = _logo_img(s, width=2.4*cm)
    info_lines = _isp_info_lines(s)

    # ── Section header helper ─────────────────────────────────────────────────
    def sec_hdr(text, w=None):
        p = Paragraph(f"  {text}", S_SEC_HDR)
        tbl = Table([[p]], colWidths=[w] if w else None)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0,0), (-1,-1), DARK),
            ("ROWPADDING",  (0,0), (-1,-1), 3),
            ("TOPPADDING",  (0,0), (-1,-1), 4),
            ("BOTTOMPADDING",(0,0),(-1,-1), 4),
        ]))
        return tbl

    def lv(label, value):
        return [Paragraph(label, S_LBL), Paragraph(str(value) if value else "—", S_VAL)]

    elems = []

    # ════════════════════════════════════════════════════════════════════════
    # BLOCK 1 — Header: Logo + ISP name/contact box
    # ════════════════════════════════════════════════════════════════════════
    isp_name_para = Paragraph(f"<b>{s.isp_name or 'ISP Billing'}</b>", S_ISP_NAME)
    isp_sub_paras = [Paragraph(ln, S_ISP_SUB) for ln in info_lines]

    logo_cell = logo if logo else Paragraph("", S_VAL)
    contact_cell = [isp_name_para] + isp_sub_paras

    hdr_tbl = Table(
        [[logo_cell, "", contact_cell]],
        colWidths=[2.8*cm, 7*cm, 8*cm]
    )
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
        ("ALIGN",       (2,0), (2,0),  "RIGHT"),
        ("BOX",         (2,0), (2,0),  1, MGRAY),
        ("BACKGROUND",  (2,0), (2,0),  LGRAY),
        ("PADDING",     (2,0), (2,0),  6),
        ("TOPPADDING",  (0,0), (-1,-1), 0),
    ]))
    elems.append(hdr_tbl)
    elems.append(HRFlowable(width="100%", thickness=2, color=CYAN, spaceAfter=6))

    # ════════════════════════════════════════════════════════════════════════
    # BLOCK 2 — Bill To (left) + Invoice Details (right)
    # ════════════════════════════════════════════════════════════════════════
    bill_to_data = [
        [Paragraph("<b>BILL TO :</b>", S_LBL)],
        lv("Customer :", cust.full_name),
        lv("Father Name :", cust.service2 or "—"),
        lv("Address :", cust.service3 or cust.area_display),
        lv("CNIC :", cust.service4 or "—"),
        lv("Phone :", cust.mobile),
    ]

    inv_no = _invoice_no(bill)

    # Payment history (last 5 months, excluding current month)
    recent_bills = sorted(
        [b for b in cust.bills if b.month != bill.month],
        key=lambda b: b.month,
        reverse=True
    )[:5]

    inv_data = [
        lv("Invoice No :",  inv_no),
        lv("Account ID :",  cust.username),
        lv("Applying Date :", _fmt_date_short(bill.created_at.date() if bill.created_at else date.today())),
        lv("Bill Month :",   _fmt_month_label(bill.month)),
        lv("Due Date :",     _fmt_date_short(bill.due_date)),
        lv("Package Type :", cust.package_display),
        lv("Status :",       cust.status.title()),
    ]

    # Build left side table
    bt_tbl = Table(bill_to_data, colWidths=[2.8*cm, 6*cm])
    bt_tbl.setStyle(TableStyle([
        ("FONTSIZE",    (0,0), (-1,-1), 8.2),
        ("TOPPADDING",  (0,0), (-1,-1), 2),
        ("BOTTOMPADDING",(0,0),(-1,-1), 2),
        ("SPAN",        (0,0), (1,0)),
    ]))

    inv_tbl = Table(inv_data, colWidths=[2.8*cm, 5.2*cm])
    inv_tbl.setStyle(TableStyle([
        ("FONTSIZE",    (0,0), (-1,-1), 8.2),
        ("TOPPADDING",  (0,0), (-1,-1), 2),
        ("BOTTOMPADDING",(0,0),(-1,-1), 2),
        ("ALIGN",       (0,0), (-1,-1), "LEFT"),
    ]))

    mid_tbl = Table([[bt_tbl, inv_tbl]], colWidths=[9*cm, 8.8*cm])
    mid_tbl.setStyle(TableStyle([
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",  (0,0), (-1,-1), 0),
    ]))
    elems.append(mid_tbl)
    elems.append(Spacer(1, 0.25*cm))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=MGRAY))
    elems.append(Spacer(1, 0.2*cm))

    # ════════════════════════════════════════════════════════════════════════
    # BLOCK 3 — Charges (left) + Payment History table (right)
    # ════════════════════════════════════════════════════════════════════════
    extras = bill.charges or []
    extra_total = sum(c.amount for c in extras)

    charges_rows = [
        [Paragraph("CHARGES", S_LBL), ""],
        [Paragraph("Fixed Broadband :", S_VAL), Paragraph(f"{bill.package_fee:,.2f}", S_VAL)],
        [Paragraph("Value Add Services :", S_VAL), Paragraph(f"{extra_total:,.2f}", S_VAL)],
    ]

    arrears = sum(b.remaining_due() for b in cust.bills
                  if b.status in ("unpaid", "partial") and b.id != bill.id)
    discount = 0.0
    after_due = 0.0

    charges_rows += [
        [Paragraph("Arrears :", S_VAL),      Paragraph(f"{arrears:,.2f}", S_VAL)],
        [Paragraph("Discount :", S_VAL),     Paragraph(f"{discount:,.2f}", S_VAL)],
        [Paragraph("After Due Date :", S_VAL), Paragraph(f"{after_due:,.2f}", S_VAL)],
    ]

    ch_tbl = Table(charges_rows, colWidths=[5.2*cm, 2.8*cm])
    ch_tbl.setStyle(TableStyle([
        ("SPAN",        (0,0), (1,0)),
        ("BACKGROUND",  (0,0), (1,0), LGRAY),
        ("FONTSIZE",    (0,0), (-1,-1), 8.2),
        ("TOPPADDING",  (0,0), (-1,-1), 2),
        ("BOTTOMPADDING",(0,0),(-1,-1), 2),
        ("ALIGN",       (1,0), (1,-1), "RIGHT"),
        ("LINEBELOW",   (0,0), (-1,-1), 0.3, MGRAY),
    ]))

    # Payment history last 5 months
    ph_header = [
        Paragraph("PAYMENT HISTORY (LAST 5 MONTHS)", S_LBL),
        "", ""
    ]
    ph_col_hdr = [
        Paragraph("Month", S_LBL),
        Paragraph("Billed", S_LBL),
        Paragraph("Paid", S_LBL),
        Paragraph("Status", S_LBL),
    ]
    ph_rows = [ph_col_hdr]
    for rb in recent_bills:
        status_col = Paragraph(
            rb.status.title(),
            sty("rbs", fontSize=8.2,
                textColor=GRN if rb.status == "paid" else RED)
        )
        ph_rows.append([
            Paragraph(_fmt_month_label(rb.month), S_VAL),
            Paragraph(f"{rb.total_amount:,.0f}", S_VAL),
            Paragraph(f"{rb.amount_paid:,.0f}", S_VAL),
            status_col,
        ])
    ph_tbl = Table(
        [[Paragraph("PAYMENT HISTORY (LAST 5 MONTHS)", S_LBL), "", "", ""]]+ph_rows,
        colWidths=[2.0*cm, 2.0*cm, 1.8*cm, 2.0*cm]
    )
    ph_tbl.setStyle(TableStyle([
        ("SPAN",        (0,0), (3,0)),
        ("BACKGROUND",  (0,0), (3,0), LGRAY),
        ("BACKGROUND",  (0,1), (3,1), DARK),
        ("TEXTCOLOR",   (0,1), (3,1), colors.white),
        ("FONTNAME",    (0,1), (3,1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8),
        ("TOPPADDING",  (0,0), (-1,-1), 2),
        ("BOTTOMPADDING",(0,0),(-1,-1), 2),
        ("LINEBELOW",   (0,0), (-1,-1), 0.3, MGRAY),
        ("ALIGN",       (1,0), (2,-1), "RIGHT"),
    ]))

    charges_section = Table(
        [[ch_tbl, Spacer(0.3*cm, 1), ph_tbl]],
        colWidths=[8.3*cm, 0.4*cm, 9.1*cm]
    )
    charges_section.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    elems.append(charges_section)
    elems.append(Spacer(1, 0.3*cm))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=MGRAY))

    # ════════════════════════════════════════════════════════════════════════
    # BLOCK 4 — Net Payable
    # ════════════════════════════════════════════════════════════════════════
    net = bill.remaining_due() if bill.status in ("unpaid", "partial") else bill.total_amount
    net_label = "Net Payable :"

    net_tbl = Table([
        [Paragraph(f"<b>{net_label}</b>", S_AMT_LBL),
         Paragraph(f"<b>{net:,.2f}</b>", S_AMT_BIG)]
    ], colWidths=[4*cm, 13.8*cm])
    net_tbl.setStyle(TableStyle([
        ("VALIGN",  (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    elems.append(net_tbl)
    elems.append(HRFlowable(width="100%", thickness=1, color=MGRAY))
    elems.append(Spacer(1, 0.25*cm))

    # ════════════════════════════════════════════════════════════════════════
    # BLOCK 5 — QR Code strip + copy labels
    # ════════════════════════════════════════════════════════════════════════
    qr_data = (f"INVOICE:{inv_no}|CUSTOMER:{cust.username}"
               f"|AMOUNT:{bill.total_amount:.0f}|STATUS:{bill.status.upper()}"
               f"|DUE:{str(bill.due_date)}")
    qr_obj = _qr_img(qr_data, size=2.2*cm)

    qr_strip = Table(
        [[qr_obj,
          Paragraph(f"<b>*{inv_no}*</b>", sty("invno", fontSize=8, alignment=TA_CENTER)),
          Paragraph("Customer Copy", S_COPY),
          ]],
        colWidths=[2.5*cm, 12*cm, 3.3*cm]
    )
    qr_strip.setStyle(TableStyle([
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("LINEABOVE",  (0,0), (-1,0), 0.5, MGRAY),
        ("LINEBELOW",  (0,0), (-1,0), 0.5, MGRAY),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    elems.append(qr_strip)

    # Company Copy line
    elems.append(Paragraph("Company Copy", S_COPY))
    elems.append(HRFlowable(width="100%", thickness=1.5,
                             color=MGRAY, dash=(4, 3), spaceAfter=8))

    # ════════════════════════════════════════════════════════════════════════
    # BLOCK 6 — Customer/Connection Info (left) + Charges Detail (right)
    # ════════════════════════════════════════════════════════════════════════
    conn_data = [
        [sec_hdr("CUSTOMER / CONNECTION INFO", w=8.5*cm)],
        [Paragraph(f"Customer : {cust.full_name}", S_VAL)],
        [Paragraph(f"Father Name : {cust.service2 or '—'}", S_VAL)],
        [Paragraph(f"Address : {cust.service3 or cust.area_display}", S_VAL)],
        [Paragraph(f"Account ID : {cust.username}", S_VAL)],
        [Paragraph(f"Billing Month : {_fmt_month_label(bill.month)}", S_VAL)],
        [Paragraph(f"Due Date : {_fmt_date_short(bill.due_date)}", S_VAL)],
        [Paragraph(f"Package Type : {cust.package_display}", S_VAL)],
    ]

    ct_tbl = Table(conn_data, colWidths=[8.5*cm])
    ct_tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("FONTSIZE",      (0,0), (-1,-1), 8.2),
    ]))

    chg_detail_data = [
        [sec_hdr("CHARGES DETAILS", w=9.3*cm)],
        [Paragraph(f"Fixed Broadband : {bill.package_fee:,.2f}", S_VAL)],
        [Paragraph(f"Value Add Services : {extra_total:,.2f}", S_VAL)],
    ]
    chg_detail_data += [
        [Paragraph(f"Arrears : {arrears:,.2f}", S_VAL)],
        [Paragraph(f"Discount : {discount:,.2f}", S_VAL)],
        [Paragraph(f"After Due Date : {after_due:,.2f}", S_VAL)],
        [Paragraph(f"Net Payable : {net:,.2f}", sty("npb", fontSize=9, fontName="Helvetica-Bold"))],
        [Spacer(1, 0.15*cm)],
        [Paragraph(
            "Dear customers, Kindly pay your bill immediately to continue "
            "our excellent service without any interruption.", S_WARN)],
    ]

    cd_tbl = Table(chg_detail_data, colWidths=[9.3*cm])
    cd_tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("FONTSIZE",      (0,0), (-1,-1), 8.2),
    ]))

    bottom = Table([[ct_tbl, Spacer(0.2*cm, 1), cd_tbl]],
                   colWidths=[8.5*cm, 0.3*cm, 9.3*cm])
    bottom.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    elems.append(bottom)
    elems.append(Spacer(1, 0.25*cm))

    # ── Footer ────────────────────────────────────────────────────────────────
    elems.append(HRFlowable(width="100%", thickness=0.5, color=MGRAY))
    elems.append(Spacer(1, 0.08 * cm))
    elems.append(Paragraph(f"*{inv_no}*", sty("inv_footer", fontSize=8, alignment=TA_CENTER)))
    if s.isp_name:
        elems.append(Paragraph(f"<b>{s.isp_name}</b>", sty("isp_footer_name", fontSize=8, alignment=TA_RIGHT)))
    contact_join = " | ".join(_isp_info_lines(s))
    if contact_join:
        elems.append(Paragraph(contact_join, sty("isp_footer_contact", fontSize=7, alignment=TA_RIGHT)))
    doc.build(elems)
    buf.seek(0)
    return buf


# ═══════════════════════════════════════════════════════════════════════════════
#  FILTERED CUSTOMERS EXPORT PDF
# ═══════════════════════════════════════════════════════════════════════════════

def build_customers_export_pdf(customers: list, s: ISPSettings,
                                title: str, filters_desc: str) -> io.BytesIO:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=1.2*cm, bottomMargin=1.2*cm)

    DARK  = colors.HexColor(s.theme_surface or "#0d1b2e")
    CYAN  = colors.HexColor(s.theme_accent or "#00a8cc")
    LGRAY = colors.HexColor("#f5f7fa")
    MGRAY = colors.HexColor("#e2e8f0")
    GRN   = colors.HexColor("#1a9e5f")
    RED   = colors.red

    def sty(name, **kw):
        base = dict(fontName="Helvetica", fontSize=8.5, textColor=colors.HexColor("#2d3748"),
                    leading=12, spaceBefore=0, spaceAfter=0)
        base.update(kw)
        return ParagraphStyle(name, **base)

    S_HDR   = sty("h", fontSize=14, fontName="Helvetica-Bold",
                   textColor=DARK, alignment=TA_LEFT)
    S_SUB   = sty("s", fontSize=8, textColor=colors.HexColor("#555"), alignment=TA_RIGHT)
    S_FILT  = sty("f", fontSize=8, textColor=colors.HexColor("#444"), alignment=TA_LEFT)
    S_TH    = sty("th", fontSize=7.8, fontName="Helvetica-Bold",
                   textColor=colors.white, alignment=TA_LEFT)
    S_TD    = sty("td", fontSize=7.8, textColor=colors.HexColor("#2d3748"))
    S_FOOT  = sty("ft", fontSize=7.5, textColor=colors.HexColor("#777"), alignment=TA_CENTER)
    S_PAID  = sty("pd", fontSize=7.8, textColor=GRN, fontName="Helvetica-Bold")
    S_UNPAD = sty("up", fontSize=7.8, textColor=RED, fontName="Helvetica-Bold")

    elems = []

    # ── Header ────────────────────────────────────────────────────────────────
    logo = _logo_img(s, width=2.2*cm)
    logo_cell = logo if logo else Paragraph("", S_TD)

    hdr_right = [
        Paragraph(f"<b>{s.isp_name}</b>",
                  sty("iname", fontSize=13, fontName="Helvetica-Bold",
                       textColor=DARK, alignment=TA_RIGHT)),
    ]
    for ln in _isp_info_lines(s):
        hdr_right.append(Paragraph(ln, S_SUB))

    hdr_tbl = Table([[logo_cell, Paragraph(f"<b>{title}</b>", S_HDR),
                      hdr_right]],
                    colWidths=[2.6*cm, 8*cm, 7.2*cm])
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN",  (0,0), (-1,-1), "TOP"),
        ("ALIGN",   (2,0), (2,0),  "RIGHT"),
    ]))
    elems.append(hdr_tbl)
    elems.append(HRFlowable(width="100%", thickness=2, color=CYAN, spaceAfter=4))

    # Filter description line
    elems.append(Paragraph(f"Filters: {filters_desc or 'None'}  |  "
                            f"Generated: {date.today().strftime('%d %b %Y')}  |  "
                            f"Total Records: {len(customers)}", S_FILT))
    elems.append(Spacer(1, 0.2*cm))

    # ── Table ─────────────────────────────────────────────────────────────────
    headers = ["#", "Name / Username", "Mobile", "Area", "Package",
               "Expires", "Status", "Due Amount"]
    th_row = [Paragraph(h, S_TH) for h in headers]

    rows = [th_row]
    for i, c in enumerate(customers, 1):
        due   = c.get("total_due", 0)
        st    = c.get("status", "active")
        bs    = c.get("bill_status_display", "")
        due_p = Paragraph(f"{due:,.0f}" if due else "—",
                          S_UNPAD if due > 0 else S_PAID)
        st_p  = Paragraph(st.title(),
                          S_PAID if st == "active" else S_UNPAD)
        rows.append([
            Paragraph(str(i), S_TD),
            Paragraph(f"<b>{c.get('full_name','')}</b><br/>"
                      f"<font size=7.5 color='#888'>{c.get('username','')}</font>",
                      S_TD),
            Paragraph(c.get("mobile","") or "—", S_TD),
            Paragraph(c.get("area_name","") or "—", S_TD),
            Paragraph(c.get("package_name","") or "—", S_TD),
            Paragraph(c.get("expiring","") or "—", S_TD),
            st_p,
            due_p,
        ])

    col_w = [0.8*cm, 5.2*cm, 2.8*cm, 2.8*cm, 2.4*cm, 2.2*cm, 1.8*cm, 2.4*cm]
    data_tbl = Table(rows, colWidths=col_w, repeatRows=1)

    tbl_style = [
        ("BACKGROUND",    (0,0), (-1,0), DARK),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.white, LGRAY]),
        ("FONTSIZE",      (0,0), (-1,-1), 7.8),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
        ("GRID",          (0,0), (-1,-1), 0.3, MGRAY),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",         (7,0), (7,-1), "RIGHT"),
    ]
    data_tbl.setStyle(TableStyle(tbl_style))
    elems.append(data_tbl)

    # ── Summary row ───────────────────────────────────────────────────────────
    total_due = sum(c.get("total_due", 0) for c in customers)
    elems.append(Spacer(1, 0.2*cm))
    summary = Table(
        [[Paragraph(f"Total Customers: <b>{len(customers)}</b>", S_FILT),
          Paragraph(f"Total Outstanding: <b>PKR {total_due:,.0f}</b>",
                    sty("ts", fontSize=9, fontName="Helvetica-Bold",
                         textColor=RED if total_due else GRN, alignment=TA_RIGHT))]],
        colWidths=[9.5*cm, 8.3*cm]
    )
    elems.append(summary)

    # ── Footer ────────────────────────────────────────────────────────────────
    elems.append(Spacer(1, 0.3*cm))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=MGRAY))
    elems.append(Paragraph(
        f"{s.isp_name}  |  {s.isp_contact}  |  {s.isp_address}  |  "
        f"Printed: {datetime.now().strftime('%d %b %Y %H:%M')}",
        S_FOOT))

    doc.build(elems)
    buf.seek(0)
    return buf


# ═══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _shift_month(d: date, months: int) -> date:
    idx = (d.year * 12 + (d.month - 1)) + months
    y = idx // 12
    m = (idx % 12) + 1
    return date(y, m, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _parse_month_key(month_key: str) -> date:
    try:
        y, m = month_key.split("-")
        y, m = int(y), int(m)
        if m < 1 or m > 12:
            raise ValueError
        return date(y, m, 1)
    except Exception:
        raise HTTPException(400, "Invalid month format. Use YYYY-MM.")


def _compute_revenue_analytics(db: Session, period: str, month_key: str = "") -> dict:
    if period not in ("month", "6months", "year"):
        raise HTTPException(400, "Invalid period. Use month, 6months, or year.")

    anchor = _parse_month_key(month_key) if month_key else _month_start(date.today())

    if period == "month":
        start = anchor
        end = _month_end(anchor)
        payments = db.query(Payment).filter(Payment.payment_date >= start, Payment.payment_date <= end).all()
        day_totals = {d: 0.0 for d in range(1, end.day + 1)}
        for p in payments:
            day_totals[p.payment_date.day] += float(p.amount or 0)
        points = [{"label": str(d), "value": round(day_totals[d], 2)} for d in range(1, end.day + 1)]
        period_label = anchor.strftime("%B %Y")
    else:
        months_count = 6 if period == "6months" else 12
        months = [_shift_month(anchor, -i) for i in range(months_count - 1, -1, -1)]
        start = months[0]
        end = _month_end(anchor)
        payments = db.query(Payment).filter(Payment.payment_date >= start, Payment.payment_date <= end).all()
        month_totals = {m.strftime("%Y-%m"): 0.0 for m in months}
        for p in payments:
            k = p.payment_date.strftime("%Y-%m")
            if k in month_totals:
                month_totals[k] += float(p.amount or 0)
        points = [{"label": m.strftime("%b %y"), "value": round(month_totals[m.strftime('%Y-%m')], 2)} for m in months]
        period_label = f"{months[0].strftime('%b %Y')} to {months[-1].strftime('%b %Y')}"

    total_revenue = round(sum(p["value"] for p in points), 2)
    payment_count = len(payments)
    avg_payment = round((total_revenue / payment_count), 2) if payment_count else 0.0
    best = max(points, key=lambda x: x["value"]) if points else {"label": "—", "value": 0.0}

    methods = {}
    for p in payments:
        key = (p.method or "Unknown").strip() or "Unknown"
        methods[key] = methods.get(key, 0.0) + float(p.amount or 0)
    method_breakdown = [{"method": m, "amount": round(v, 2)} for m, v in sorted(methods.items(), key=lambda x: x[1], reverse=True)]

    return {
        "period": period,
        "period_label": period_label,
        "start_date": str(start),
        "end_date": str(end),
        "total_revenue": total_revenue,
        "payment_count": payment_count,
        "avg_payment": avg_payment,
        "best_point": best,
        "points": points,
        "method_breakdown": method_breakdown,
    }


def build_revenue_report_pdf(report: dict, s: ISPSettings) -> io.BytesIO:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1.4 * cm,
        rightMargin=1.4 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
    )

    DARK = colors.HexColor("#0d1b2e")
    MGRAY = colors.HexColor("#dfe3ea")
    ACNT = colors.HexColor((s.theme_accent or "#00d4ff"))
    styles = getSampleStyleSheet()
    S_H1 = ParagraphStyle("rh1", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=16, textColor=DARK, spaceAfter=3)
    S_SUB = ParagraphStyle("rsub", parent=styles["BodyText"], fontSize=9, textColor=colors.HexColor("#4b5563"), spaceAfter=8)
    S_TXT = ParagraphStyle("rtxt", parent=styles["BodyText"], fontSize=9, textColor=colors.HexColor("#334155"))

    elems = []
    logo = _logo_img(s, width=2.0 * cm)
    title_block = [
        Paragraph(f"<b>{s.isp_name or 'ISP Billing'}</b>", S_H1),
        Paragraph(f"Revenue Analytics Report ({report.get('period_label', '')})", S_SUB),
        Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}", S_TXT),
    ]
    hdr = Table([[logo if logo else "", title_block]], colWidths=[2.4 * cm, 14.8 * cm])
    hdr.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elems.append(hdr)
    elems.append(Spacer(1, 0.25 * cm))

    summary = Table(
        [
            ["Total Revenue", "Payments", "Average Payment", "Best Period Point"],
            [
                f"PKR {report['total_revenue']:,.0f}",
                str(report["payment_count"]),
                f"PKR {report['avg_payment']:,.0f}",
                f"{report['best_point']['label']} ({report['best_point']['value']:,.0f})",
            ],
        ],
        colWidths=[4.0 * cm, 3.0 * cm, 4.0 * cm, 6.2 * cm],
    )
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, MGRAY),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8fafc")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
    ]))
    elems.append(summary)
    elems.append(Spacer(1, 0.35 * cm))

    points = report.get("points", [])
    if points:
        chart = VerticalBarChart()
        chart.x = 40
        chart.y = 35
        chart.height = 150
        chart.width = 470
        chart.data = [[p["value"] for p in points]]
        chart.categoryAxis.categoryNames = [p["label"] for p in points]
        chart.valueAxis.valueMin = 0
        chart.valueAxis.forceZero = True
        chart.bars[0].fillColor = ACNT
        chart.categoryAxis.labels.fontSize = 6 if len(points) > 12 else 7
        chart.categoryAxis.labels.angle = 45 if len(points) > 12 else 0
        chart.categoryAxis.labels.dy = -8
        drawing = Drawing(540, 210)
        drawing.add(chart)
        elems.append(Paragraph("<b>Revenue Trend</b>", S_TXT))
        elems.append(drawing)
        elems.append(Spacer(1, 0.2 * cm))

    methods = report.get("method_breakdown", [])
    if methods:
        pie_d = Drawing(540, 200)
        pie = Pie()
        pie.x = 60
        pie.y = 25
        pie.width = 170
        pie.height = 140
        pie.data = [m["amount"] for m in methods[:6]]
        pie.labels = [m["method"] for m in methods[:6]]
        pie.sideLabels = True
        pie.slices.strokeWidth = 0.3
        pie_d.add(pie)

        method_rows = [["Method", "Amount (PKR)"]] + [[m["method"], f"{m['amount']:,.0f}"] for m in methods]
        method_tbl = Table(method_rows, colWidths=[6.5 * cm, 3.5 * cm])
        method_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.4, MGRAY),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ]))

        elems.append(Paragraph("<b>Payment Method Breakdown</b>", S_TXT))
        elems.append(Table([[pie_d, method_tbl]], colWidths=[9.2 * cm, 7.8 * cm]))

    doc.build(elems)
    buf.seek(0)
    return buf


@app.get("/api/revenue-analytics")
def revenue_analytics(
    period: str = Query("6months"),
    month: str = Query(""),
    db: Session = Depends(get_db),
):
    return _compute_revenue_analytics(db, period=period, month_key=month)


@app.get("/api/revenue-report-pdf")
def revenue_report_pdf(
    period: str = Query("6months"),
    month: str = Query(""),
    db: Session = Depends(get_db),
):
    report = _compute_revenue_analytics(db, period=period, month_key=month)
    s = db.query(ISPSettings).get(1) or ISPSettings()
    buf = build_revenue_report_pdf(report, s)
    log_activity(db, f"Revenue report exported ({period})", "bill")
    fname = f"Revenue_{period}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    today_ = date.today()
    month  = today_.strftime("%Y-%m")
    total_customers = db.query(Customer).filter(Customer.status == "active").count()
    month_bills  = db.query(Bill).filter(Bill.month == month).all()
    paid_bills   = [b for b in month_bills if b.status == "paid"]
    unpaid_bills = db.query(Bill).filter(Bill.status.in_(["unpaid", "partial"])).all()
    overdue_bills = [b for b in unpaid_bills if b.is_overdue()]
    unpaid_charges = db.query(ExtraCharge).filter(ExtraCharge.status == "unpaid").all()
    areas    = db.query(Area).all()
    packages = db.query(Package).all()
    recent   = db.query(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(15).all()
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
                              "due_date": str(b.due_date) if b.due_date else ""}
                            for b in overdue_bills[:20]],
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
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Area name is required")
    if db.query(Area).filter(Area.name == name).first():
        raise HTTPException(400, "Area already exists")
    a = Area(name=name)
    db.add(a); db.commit(); db.refresh(a)
    log_activity(db, f"Area '{a.name}' created", "system")
    return a.to_dict()

@app.put("/api/areas/{area_id}")
def update_area(area_id: str, data: AreaIn, db: Session = Depends(get_db)):
    a = db.query(Area).get(area_id)
    if not a: raise HTTPException(404, "Not found")
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Area name is required")
    conflict = db.query(Area).filter(Area.name == name, Area.id != area_id).first()
    if conflict:
        raise HTTPException(400, "Area already exists")
    a.name = name; db.commit(); db.refresh(a)
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
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Package name is required")
    if db.query(Package).filter(Package.name == name).first():
        raise HTTPException(400, "Package already exists")
    payload = data.model_dump()
    payload["name"] = name
    p = Package(**payload)
    db.add(p); db.commit(); db.refresh(p)
    log_activity(db, f"Package '{p.name}' created", "system")
    return p.to_dict()

@app.put("/api/packages/{pkg_id}")
def update_package(pkg_id: str, data: PackageIn, db: Session = Depends(get_db)):
    p = db.query(Package).get(pkg_id)
    if not p: raise HTTPException(404, "Not found")
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Package name is required")
    conflict = db.query(Package).filter(Package.name == name, Package.id != pkg_id).first()
    if conflict:
        raise HTTPException(400, "Package already exists")
    payload = data.model_dump()
    payload["name"] = name
    for k, v in payload.items(): setattr(p, k, v)
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
    area_id: str = "",
    status: str = "",
    has_dues: str = "",
    bill_status: str = "",
    month: str = "",
    db: Session = Depends(get_db),
):
    q = db.query(Customer)
    if search:
        like = f"%{search}%"
        q = q.filter(Customer.full_name.ilike(like) |
                     Customer.username.ilike(like) |
                     Customer.mobile.ilike(like))
    if area_name:
        q = q.join(Area, Customer.area_id == Area.id).filter(Area.name == area_name)
    elif area_id:
        q = q.filter(Customer.area_id == area_id)
    if status:
        q = q.filter(Customer.status == status)
    customers = q.order_by(Customer.full_name).all()
    if has_dues == "1":
        customers = [c for c in customers if c.has_dues()]
    if bill_status:
        target_month = month if month else date.today().strftime("%Y-%m")
        if bill_status == "paid":
            customers = [c for c in customers if any(b.month == target_month and b.status == "paid" for b in c.bills)]
        elif bill_status == "unpaid":
            customers = [c for c in customers if any(b.month == target_month and b.status == "unpaid" for b in c.bills)]
        elif bill_status == "partial":
            customers = [c for c in customers if any(b.month == target_month and b.status == "partial" for b in c.bills)]
        elif bill_status == "overdue":
            customers = [c for c in customers if any(b.is_overdue() for b in c.bills)]
        elif bill_status == "no_bill":
            customers = [c for c in customers if not any(b.month == target_month for b in c.bills)]
    return [c.to_dict() for c in customers]

@app.get("/api/customers/{cust_id}")
def get_customer(cust_id: str, db: Session = Depends(get_db)):
    c = db.query(Customer).get(cust_id)
    if not c: raise HTTPException(404, "Not found")
    d = c.to_dict()
    d["bills"] = [b.to_dict() for b in sorted(c.bills, key=lambda x: x.month, reverse=True)]
    return d

def _validate_customer_relations(data: CustomerIn, db: Session):
    if data.package_id and not db.query(Package).get(data.package_id):
        raise HTTPException(400, "Invalid package_id")
    if data.area_id and not db.query(Area).get(data.area_id):
        raise HTTPException(400, "Invalid area_id")

@app.post("/api/customers")
def create_customer(data: CustomerIn, db: Session = Depends(get_db)):
    if db.query(Customer).filter(Customer.username == data.username).first():
        raise HTTPException(400, "Username already exists")
    _validate_customer_relations(data, db)
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
    if data.username != c.username:
        if db.query(Customer).filter(Customer.username == data.username).first():
            raise HTTPException(400, "Username already exists")
    _validate_customer_relations(data, db)
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


# ─── EXPORT FILTERED CUSTOMERS AS PDF ────────────────────────────────────────
@app.post("/api/customers/export-pdf")
def export_customers_pdf(data: ExportCustomersIn, db: Session = Depends(get_db)):
    """Export the currently filtered customer list as a branded PDF."""
    s = db.query(ISPSettings).get(1) or ISPSettings()

    q = db.query(Customer)
    if data.search:
        like = f"%{data.search}%"
        q = q.filter(Customer.full_name.ilike(like) |
                     Customer.username.ilike(like) |
                     Customer.mobile.ilike(like))
    if data.area_name:
        q = q.join(Area, Customer.area_id == Area.id).filter(Area.name == data.area_name)
    if data.status:
        q = q.filter(Customer.status == data.status)
    customers = q.order_by(Customer.full_name).all()

    if data.bill_status:
        target_month = data.month if data.month else date.today().strftime("%Y-%m")
        if data.bill_status == "paid":
            customers = [c for c in customers if any(b.month == target_month and b.status == "paid" for b in c.bills)]
        elif data.bill_status == "unpaid":
            customers = [c for c in customers if any(b.month == target_month and b.status == "unpaid" for b in c.bills)]
        elif data.bill_status == "partial":
            customers = [c for c in customers if any(b.month == target_month and b.status == "partial" for b in c.bills)]
        elif data.bill_status == "overdue":
            customers = [c for c in customers if any(b.is_overdue() for b in c.bills)]
        elif data.bill_status == "no_bill":
            customers = [c for c in customers if not any(b.month == target_month for b in c.bills)]

    filter_parts = []
    if data.search:      filter_parts.append(f"Search: {data.search}")
    if data.area_name:   filter_parts.append(f"Area: {data.area_name}")
    if data.bill_status: filter_parts.append(f"Bill: {data.bill_status}")
    if data.month:       filter_parts.append(f"Month: {data.month}")
    if data.status:      filter_parts.append(f"Status: {data.status}")
    filters_desc = "  |  ".join(filter_parts) if filter_parts else "All customers"

    custs_dicts = [c.to_dict() for c in customers]
    buf = build_customers_export_pdf(custs_dicts, s, data.title or "Customer Report", filters_desc)
    fname = f"Customers_{date.today().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'inline; filename="{fname}"'})


# ─── UPLOAD LOGO ──────────────────────────────────────────────────────────────
@app.post("/api/upload-logo")
async def upload_logo(file: UploadFile = File(...)):
    """Accept PNG/JPG logo and save to user data directory."""
    data_dir = os.path.dirname(DB_PATH)
    os.makedirs(data_dir, exist_ok=True)
    dest = os.path.join(data_dir, "logo.png")
    content = await file.read()
    # Convert to PNG using Pillow
    from PIL import Image as PILImage
    img = PILImage.open(io.BytesIO(content)).convert("RGBA")
    img.save(dest, "PNG")
    return {"ok": True, "message": "Logo uploaded successfully"}

@app.get("/api/logo-exists")
def logo_exists():
    path = _logo_path()
    return {"exists": os.path.exists(path)}


@app.get("/api/logo")
def get_logo():
    path = _logo_path()
    if not os.path.exists(path):
        raise HTTPException(404, "Logo not found")
    return FileResponse(path)


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
        if not username: skipped += 1; continue
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
                area_obj = Area(name=area_name.strip()); db.add(area_obj); db.flush()
        pkg_obj = None
        if pkg_name:
            pkg_obj = db.query(Package).filter(Package.name == pkg_name.strip()).first()
            if not pkg_obj:
                pkg_obj = Package(name=pkg_name.strip(), speed=pkg_name.strip()); db.add(pkg_obj); db.flush()
        try:
            existing = db.query(Customer).filter(Customer.username == username).first()
            if existing:
                existing.sr_no=sr_no; existing.full_name=full_name; existing.mobile=mobile
                existing.expiring=exp_date; existing.package_id=pkg_obj.id if pkg_obj else None
                existing.package_name_raw=pkg_name; existing.area_id=area_obj.id if area_obj else None
                existing.area_name_raw=area_name; existing.service2=svc2
                existing.service3=svc3; existing.service4=svc4; existing.imported_at=datetime.utcnow()
                updated += 1
            else:
                c = Customer(sr_no=sr_no, username=username, full_name=full_name, mobile=mobile,
                             expiring=exp_date, package_id=pkg_obj.id if pkg_obj else None,
                             package_name_raw=pkg_name, area_id=area_obj.id if area_obj else None,
                             area_name_raw=area_name, service2=svc2, service3=svc3, service4=svc4,
                             imported_at=datetime.utcnow())
                db.add(c); added += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")
    db.commit()
    log_activity(db, f"CSV import: {added} added, {updated} updated", "import")
    return {"added": added, "updated": updated, "skipped": skipped, "errors": errors[:20]}


# ─── BILLS ────────────────────────────────────────────────────────────────────
@app.get("/api/bills/{bill_id}")
def get_bill(bill_id: str, db: Session = Depends(get_db)):
    bill = db.query(Bill).get(bill_id)
    if not bill: raise HTTPException(404, "Bill not found")
    return bill.to_dict()

@app.put("/api/bills/{bill_id}")
def update_bill(bill_id: str, data: BillUpdateIn, db: Session = Depends(get_db)):
    bill = db.query(Bill).get(bill_id)
    if not bill: raise HTTPException(404, "Bill not found")
    package_fee_changed = False
    if data.month:
        bill.month = data.month
    if data.package_fee is not None:
        bill.package_fee = data.package_fee
        package_fee_changed = True
    if data.due_date is not None:
        if data.due_date == "":
            bill.due_date = None
        else:
            try:
                bill.due_date = date.fromisoformat(data.due_date)
            except ValueError:
                raise HTTPException(400, "Invalid due_date format. Expected YYYY-MM-DD")
    if data.notes is not None:
        bill.notes = data.notes

    if package_fee_changed:
        bill.recalc(db)
    else:
        db.commit()
    db.refresh(bill)
    return bill.to_dict()

@app.get("/api/bills")
def list_bills(search: str="", month: str="", status: str="",
               area: str="", customer_id: str="", overdue: str="",
               db: Session = Depends(get_db)):
    q = db.query(Bill)
    if customer_id: q = q.filter(Bill.customer_id == customer_id)
    if month:       q = q.filter(Bill.month == month)
    if status:      q = q.filter(Bill.status == status)
    if search:
        q = q.join(Customer).filter(Customer.full_name.ilike(f"%{search}%") |
                                     Customer.username.ilike(f"%{search}%"))
    if area:
        q = q.join(Customer).join(Area).filter(Area.name == area)
    bills = q.order_by(Bill.month.desc()).all()
    if overdue == "1": bills = [b for b in bills if b.is_overdue()]
    return [b.to_dict() for b in bills]

@app.post("/api/bills")
def create_bill(data: BillIn, db: Session = Depends(get_db)):
    customer = db.query(Customer).get(data.customer_id)
    if not customer:
        raise HTTPException(400, "Customer not found")
    existing = db.query(Bill).filter(Bill.customer_id==data.customer_id, Bill.month==data.month).first()
    if existing: return existing.to_dict()
    due = None
    if data.due_date:
        try:
            due = date.fromisoformat(data.due_date)
        except ValueError:
            raise HTTPException(400, "Invalid due_date format. Expected YYYY-MM-DD")
    b = Bill(customer_id=data.customer_id, month=data.month,
             package_fee=data.package_fee, total_amount=data.package_fee, due_date=due, notes=data.notes)
    db.add(b); db.commit(); db.refresh(b)
    return b.to_dict()

@app.delete("/api/bills/{bill_id}")
def delete_bill(bill_id: str, db: Session = Depends(get_db)):
    bill = db.query(Bill).get(bill_id)
    if not bill: raise HTTPException(404, "Bill not found")
    customer_name = bill.customer.full_name if bill.customer else bill.customer_id
    bill_month = bill.month
    db.delete(bill)
    db.commit()
    log_activity(db, f"Bill deleted for {customer_name} ({bill_month})", "bill")
    return {"ok": True}

@app.post("/api/generate-bills")
def generate_bills(data: GenerateBillsIn, db: Session = Depends(get_db)):
    year, mon = map(int, data.month.split("-"))
    last_day  = calendar.monthrange(year, mon)[1]
    due_date_ = date(year, mon, min(data.due_day, last_day))
    customers = db.query(Customer).filter(Customer.status == "active").all()
    created = skipped = 0
    for c in customers:
        ex = db.query(Bill).filter(Bill.customer_id==c.id, Bill.month==data.month).first()
        if ex: skipped += 1; continue
        b = Bill(customer_id=c.id, month=data.month, package_fee=c.package_fee,
                 total_amount=c.package_fee, due_date=due_date_)
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
    db.add(p); db.flush(); bill.recalc(db)
    if bill.status == "paid":
        bill.paid_date=pay_date; bill.paid_method=data.method; bill.paid_ref=data.reference; db.commit()
    log_activity(db, f"Payment PKR {data.amount:,.0f} for {bill.customer.full_name} ({bill.month})", "payment")
    return p.to_dict()


# ─── EXTRA CHARGES ────────────────────────────────────────────────────────────
@app.post("/api/charges")
def add_charge(data: ChargeIn, db: Session = Depends(get_db)):
    bill = db.query(Bill).get(data.bill_id)
    if not bill: raise HTTPException(404, "Bill not found")
    chg_date = date.fromisoformat(data.charge_date) if data.charge_date else date.today()
    c = ExtraCharge(bill_id=data.bill_id, charge_type=data.charge_type,
                    description=data.description, amount=data.amount, charge_date=chg_date)
    db.add(c); db.flush(); bill.recalc(db)
    log_activity(db, f"Charge {data.charge_type} PKR {data.amount:,.0f} added for {bill.customer.full_name}", "charge")
    return c.to_dict()

@app.post("/api/charges/{charge_id}/mark-paid")
def mark_charge_paid(charge_id: str, db: Session = Depends(get_db)):
    c = db.query(ExtraCharge).get(charge_id)
    if not c: raise HTTPException(404)
    c.status="paid"; c.paid_date=date.today(); db.commit(); c.bill.recalc(db)
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
        Customer.status == "active", Customer.expiring != None,
        Customer.expiring >= today_, Customer.expiring <= threshold,
    ).order_by(Customer.expiring).all()
    result = []
    for c in custs:
        days_left = (c.expiring - today_).days if c.expiring else None
        result.append({"id": c.id, "username": c.username, "full_name": c.full_name,
                       "mobile": c.mobile, "mobile_e164": c.get_mobile_e164(),
                       "package": c.package_display, "area": c.area_display,
                       "expiring": str(c.expiring), "days_left": days_left,
                       "unpaid_amt": c.total_due()})
    return result

@app.get("/api/reminders")
def list_reminders(db: Session = Depends(get_db)):
    return [r.to_dict() for r in db.query(ReminderLog).order_by(ReminderLog.sent_at.desc()).limit(100).all()]


# ─── SETTINGS ─────────────────────────────────────────────────────────────────
@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    s = db.query(ISPSettings).get(1)
    if not s: s = ISPSettings(id=1); db.add(s); db.commit(); db.refresh(s)
    return s.to_dict()

@app.patch("/api/settings")
def update_settings(data: SettingsIn, db: Session = Depends(get_db)):
    s = db.query(ISPSettings).get(1)
    if not s: s = ISPSettings(id=1); db.add(s)
    for k, v in data.model_dump().items():
        if v is not None: setattr(s, k, v)
    db.commit(); db.refresh(s)
    log_activity(db, "ISP settings updated", "system")
    return s.to_dict()


# ═══════════════════════════════════════════════════════════════════════════════
#  PDF ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/bills/{bill_id}/pdf")
def generate_pdf(bill_id: str, db: Session = Depends(get_db)):
    bill = db.query(Bill).get(bill_id)
    if not bill: raise HTTPException(404, "Bill not found")
    s = db.query(ISPSettings).get(1) or ISPSettings()
    buf = build_bill_pdf(bill, s)
    fname = f"Bill_{bill.customer.username}_{bill.month}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'inline; filename="{fname}"'})


@app.get("/api/customers/{cust_id}/bill-pdf")
def generate_customer_bill_pdf(cust_id: str, db: Session = Depends(get_db)):
    cust = db.query(Customer).get(cust_id)
    if not cust: raise HTTPException(404, "Customer not found")
    s = db.query(ISPSettings).get(1) or ISPSettings()
    unpaid = [b for b in cust.bills if b.status in ("unpaid","partial")]
    if not unpaid: raise HTTPException(404, "No unpaid bills")
    # Generate PDFs for each unpaid bill and merge
    if len(unpaid) == 1:
        buf = build_bill_pdf(unpaid[0], s)
    else:
        from PyPDF2 import PdfMerger
        merger = PdfMerger()
        for b in sorted(unpaid, key=lambda x: x.month, reverse=True):
            merger.append(build_bill_pdf(b, s))
        buf = io.BytesIO()
        merger.write(buf)
        buf.seek(0)
    fname = f"Bills_{cust.username}_unpaid.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'inline; filename="{fname}"'})


# ═══════════════════════════════════════════════════════════════════════════════
#  DATABASE BACKUP & RESTORE
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/backup/download")
def download_backup(db: Session = Depends(get_db)):
    """
    Download a timestamped copy of the SQLite database file.
    The file is safe to save and restore later via /api/backup/restore.
    """
    if not os.path.exists(DB_PATH):
        raise HTTPException(404, "Database file not found")

    # Flush all pending writes by running a checkpoint
    try:
        db.execute(text("PRAGMA wal_checkpoint(FULL)"))
    except Exception:
        pass

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"ssnet_backup_{timestamp}.db"

    # Read into memory so we can stream it
    with open(DB_PATH, "rb") as f:
        data = f.read()

    buf = io.BytesIO(data)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/backup/restore")
async def restore_backup(file: UploadFile = File(...)):
    """
    Restore the database from an uploaded .db backup file.
    ⚠️  This REPLACES the current database. App restart recommended after restore.
    """
    content = await file.read()

    # Validate it's a SQLite file (magic bytes: "SQLite format 3")
    if not content.startswith(b"SQLite format 3"):
        raise HTTPException(400, "Invalid file: not a SQLite database")

    # Write the backup to a temp file first, then move atomically
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.write(content)
    tmp.close()

    try:
        # Release SQLite file handles before replacing DB (important on Windows)
        ENGINE.dispose()

        # Keep a rolling backup of the current DB just in case
        if os.path.exists(DB_PATH):
            rollback_path = DB_PATH + ".pre_restore"
            shutil.copy2(DB_PATH, rollback_path)

        shutil.move(tmp.name, DB_PATH)

        # Remove stale SQLite sidecar files so restored DB opens cleanly
        for suffix in ("-wal", "-shm"):
            sidecar = DB_PATH + suffix
            if os.path.exists(sidecar):
                try:
                    os.remove(sidecar)
                except Exception:
                    pass
    except Exception:
        raise HTTPException(500, "Restore failed. Close the app and try again.")
    finally:
        if os.path.exists(tmp.name):
            try:
                os.remove(tmp.name)
            except Exception:
                pass

    return {
        "ok": True,
        "message": "Database restored successfully. Please restart the app to reload all data.",
        "restored_size_kb": round(len(content) / 1024, 1),
    }


@app.get("/api/backup/info")
def backup_info():
    """Return metadata about the current database file."""
    if not os.path.exists(DB_PATH):
        return {"exists": False}
    stat = os.stat(DB_PATH)
    return {
        "exists": True,
        "path": DB_PATH,
        "size_kb": round(stat.st_size / 1024, 1),
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%d %b %Y %H:%M:%S"),
    }


# ─── STATIC FILES & INDEX ─────────────────────────────────────────────────────
import os as _os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_STATIC = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "static")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

@app.get("/")
def serve_index():
    return FileResponse(_os.path.join(_STATIC, "index.html"))

