"""
SS Net ISP — SQLAlchemy Database Models (SQLite embedded)
All tables for the desktop application.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import (
    create_engine, Column, String, Integer, Float, Boolean,
    Date, DateTime, Text, ForeignKey, UniqueConstraint, event
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session
from sqlalchemy.sql import func
import os

Base = declarative_base()


def new_id():
    return str(uuid.uuid4())


# ─── AREA ────────────────────────────────────────────────────────────────────
class Area(Base):
    __tablename__ = "areas"
    id         = Column(String(36), primary_key=True, default=new_id)
    name       = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    customers  = relationship("Customer", back_populates="area_rel")

    def to_dict(self):
        return {"id": self.id, "name": self.name,
                "customer_count": len(self.customers),
                "created_at": str(self.created_at)}


# ─── PACKAGE ─────────────────────────────────────────────────────────────────
class Package(Base):
    __tablename__ = "packages"
    id          = Column(String(36), primary_key=True, default=new_id)
    name        = Column(String(100), unique=True, nullable=False)
    speed       = Column(String(50), default="")
    monthly_fee = Column(Float, default=0.0)
    description = Column(Text, default="")
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    customers   = relationship("Customer", back_populates="package_rel")

    def to_dict(self):
        return {"id": self.id, "name": self.name, "speed": self.speed,
                "monthly_fee": self.monthly_fee, "description": self.description,
                "is_active": self.is_active,
                "customer_count": len(self.customers),
                "created_at": str(self.created_at)}


# ─── CUSTOMER ─────────────────────────────────────────────────────────────────
class Customer(Base):
    __tablename__ = "customers"
    id               = Column(String(36), primary_key=True, default=new_id)
    sr_no            = Column(String(20), default="")
    username         = Column(String(100), unique=True, nullable=False, index=True)
    full_name        = Column(String(200), nullable=False)
    mobile           = Column(String(30), default="")
    expiring         = Column(Date, nullable=True)
    package_id       = Column(String(36), ForeignKey("packages.id"), nullable=True)
    package_name_raw = Column(String(100), default="")
    area_id          = Column(String(36), ForeignKey("areas.id"), nullable=True)
    area_name_raw    = Column(String(100), default="")
    service2         = Column(String(200), default="")
    service3         = Column(String(200), default="")
    service4         = Column(String(200), default="")
    status           = Column(String(20), default="active")   # active/suspended/disconnected
    notes            = Column(Text, default="")
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    imported_at      = Column(DateTime, nullable=True)

    area_rel    = relationship("Area",    back_populates="customers")
    package_rel = relationship("Package", back_populates="customers")
    bills       = relationship("Bill",    back_populates="customer", cascade="all, delete-orphan")
    reminders   = relationship("ReminderLog", back_populates="customer", cascade="all, delete-orphan")

    @property
    def area_display(self):
        return self.area_rel.name if self.area_rel else (self.area_name_raw or "—")

    @property
    def package_display(self):
        return self.package_rel.name if self.package_rel else (self.package_name_raw or "—")

    @property
    def package_fee(self):
        return self.package_rel.monthly_fee if self.package_rel else 0.0

    def total_due(self):
        return sum(b.remaining_due() for b in self.bills if b.status in ("unpaid", "partial"))

    def has_dues(self):
        return any(b.status in ("unpaid", "partial") for b in self.bills)

    def get_mobile_e164(self):
        m = self.mobile.replace(" ", "").replace("-", "")
        if m.startswith("0"):
            m = "92" + m[1:]
        elif not m.startswith("92"):
            m = "92" + m
        return m

    def to_dict(self):
        return {
            "id": self.id, "sr_no": self.sr_no, "username": self.username,
            "full_name": self.full_name, "mobile": self.mobile,
            "expiring": str(self.expiring) if self.expiring else None,
            "area_name": self.area_display, "area_id": self.area_id,
            "package_name": self.package_display, "package_id": self.package_id,
            "package_fee": self.package_fee,
            "package_name_raw": self.package_name_raw,
            "service2": self.service2, "service3": self.service3, "service4": self.service4,
            "status": self.status, "notes": self.notes,
            "total_due": self.total_due(), "has_dues": self.has_dues(),
            "created_at": str(self.created_at),
        }


# ─── BILL ─────────────────────────────────────────────────────────────────────
class Bill(Base):
    __tablename__ = "bills"
    __table_args__ = (UniqueConstraint("customer_id", "month", name="uq_customer_month"),)

    id           = Column(String(36), primary_key=True, default=new_id)
    customer_id  = Column(String(36), ForeignKey("customers.id"), nullable=False, index=True)
    month        = Column(String(7), nullable=False, index=True)   # YYYY-MM
    package_fee  = Column(Float, default=0.0)
    total_amount = Column(Float, default=0.0)
    amount_paid  = Column(Float, default=0.0)
    status       = Column(String(10), default="unpaid")   # unpaid/partial/paid
    due_date     = Column(Date, nullable=True)
    paid_date    = Column(Date, nullable=True)
    paid_method  = Column(String(50), default="")
    paid_ref     = Column(String(100), default="")
    notes        = Column(Text, default="")
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="bills")
    charges  = relationship("ExtraCharge", back_populates="bill", cascade="all, delete-orphan")
    payments = relationship("Payment",     back_populates="bill", cascade="all, delete-orphan")
    reminders = relationship("ReminderLog", back_populates="bill")

    def remaining_due(self):
        return max(0.0, self.total_amount - self.amount_paid)

    def recalc(self, session):
        extras = sum(c.amount for c in self.charges)
        self.total_amount = self.package_fee + extras
        paid = sum(p.amount for p in self.payments)
        self.amount_paid = paid
        if paid <= 0:
            self.status = "unpaid"
        elif paid >= self.total_amount:
            self.status = "paid"
        else:
            self.status = "partial"
        session.commit()

    def is_overdue(self):
        if self.status == "paid":
            return False
        return bool(self.due_date and self.due_date < date.today())

    def to_dict(self):
        c = self.customer
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": c.full_name if c else "",
            "customer_username": c.username if c else "",
            "customer_mobile": c.mobile if c else "",
            "customer_area": c.area_display if c else "",
            "customer_package": c.package_display if c else "",
            "month": self.month,
            "package_fee": self.package_fee,
            "total_amount": self.total_amount,
            "amount_paid": self.amount_paid,
            "remaining_due": self.remaining_due(),
            "status": self.status,
            "due_date": str(self.due_date) if self.due_date else None,
            "paid_date": str(self.paid_date) if self.paid_date else None,
            "paid_method": self.paid_method,
            "paid_ref": self.paid_ref,
            "notes": self.notes,
            "is_overdue": self.is_overdue(),
            "charges": [ch.to_dict() for ch in self.charges],
            "payments": [p.to_dict() for p in self.payments],
            "created_at": str(self.created_at),
        }


# ─── EXTRA CHARGE ─────────────────────────────────────────────────────────────
class ExtraCharge(Base):
    __tablename__ = "extra_charges"
    id          = Column(String(36), primary_key=True, default=new_id)
    bill_id     = Column(String(36), ForeignKey("bills.id"), nullable=False)
    charge_type = Column(String(50), default="Other")
    description = Column(String(200), default="")
    amount      = Column(Float, nullable=False)
    status      = Column(String(10), default="unpaid")
    charge_date = Column(Date, default=date.today)
    paid_date   = Column(Date, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    bill = relationship("Bill", back_populates="charges")

    def to_dict(self):
        return {"id": self.id, "bill_id": self.bill_id, "charge_type": self.charge_type,
                "description": self.description, "amount": self.amount,
                "status": self.status, "charge_date": str(self.charge_date),
                "paid_date": str(self.paid_date) if self.paid_date else None}


# ─── PAYMENT ──────────────────────────────────────────────────────────────────
class Payment(Base):
    __tablename__ = "payments"
    id           = Column(String(36), primary_key=True, default=new_id)
    bill_id      = Column(String(36), ForeignKey("bills.id"), nullable=False)
    amount       = Column(Float, nullable=False)
    method       = Column(String(30), default="Cash")
    reference    = Column(String(100), default="")
    payment_date = Column(Date, default=date.today)
    notes        = Column(Text, default="")
    created_at   = Column(DateTime, default=datetime.utcnow)

    bill = relationship("Bill", back_populates="payments")

    def to_dict(self):
        return {"id": self.id, "bill_id": self.bill_id, "amount": self.amount,
                "method": self.method, "reference": self.reference,
                "payment_date": str(self.payment_date), "notes": self.notes}


# ─── REMINDER LOG ─────────────────────────────────────────────────────────────
class ReminderLog(Base):
    __tablename__ = "reminder_logs"
    id          = Column(String(36), primary_key=True, default=new_id)
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False)
    bill_id     = Column(String(36), ForeignKey("bills.id"), nullable=True)
    channel     = Column(String(20), default="whatsapp")
    message     = Column(Text, default="")
    sent_at     = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="reminders")
    bill     = relationship("Bill",     back_populates="reminders")

    def to_dict(self):
        return {"id": self.id, "customer_id": self.customer_id,
                "customer_name": self.customer.full_name if self.customer else "",
                "channel": self.channel, "message": self.message,
                "sent_at": str(self.sent_at)}


# ─── ACTIVITY LOG ─────────────────────────────────────────────────────────────
class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id         = Column(String(36), primary_key=True, default=new_id)
    action     = Column(String(300), nullable=False)
    icon_type  = Column(String(20), default="system")
    created_at = Column(DateTime, default=datetime.utcnow)

    ICONS = {"payment":"✅","import":"📂","reminder":"📱","bill":"💰",
             "charge":"💳","customer":"👤","system":"⚙️"}

    @property
    def icon(self):
        return self.ICONS.get(self.icon_type, "📝")

    def to_dict(self):
        return {"id": self.id, "action": self.action, "icon": self.icon,
                "icon_type": self.icon_type, "created_at": str(self.created_at)}


# ─── ISP SETTINGS ─────────────────────────────────────────────────────────────
class ISPSettings(Base):
    __tablename__ = "isp_settings"
    id                = Column(Integer, primary_key=True, default=1)
    isp_name          = Column(String(100), default="NetPulse ISP")
    isp_contact       = Column(String(50),  default="03001234567")
    isp_address       = Column(Text,        default="")
    reminder_days     = Column(Integer,     default=7)
    reminder_template = Column(Text, default=(
        "Dear {name}, your internet bill of PKR {amount} for package {package} "
        "is due on {date}. Please pay to avoid disconnection. "
        "Contact: {isp_contact}. Thank you!"))
    jazzcash_account  = Column(String(50),  default="")
    easypaisa_account = Column(String(50),  default="")
    bank_account      = Column(String(100), default="")
    bank_name         = Column(String(100), default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {"isp_name": self.isp_name, "isp_contact": self.isp_contact,
                "isp_address": self.isp_address, "reminder_days": self.reminder_days,
                "reminder_template": self.reminder_template,
                "jazzcash_account": self.jazzcash_account or "",
                "easypaisa_account": self.easypaisa_account or "",
                "bank_account": self.bank_account or "",
                "bank_name": self.bank_name or ""}


# ─── DATABASE ENGINE ──────────────────────────────────────────────────────────
def get_db_path():
    """Store SQLite DB in user's home directory so data persists across updates."""
    home = os.path.expanduser("~")
    data_dir = os.path.join(home, ".netpulse")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "netpulse.db")


DB_PATH  = get_db_path()
ENGINE   = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=ENGINE, autocommit=False, autoflush=False)


def _ensure_columns():
    """Add new columns to existing tables (SQLite ALTER TABLE)."""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    existing = {row[1] for row in cur.execute("PRAGMA table_info(isp_settings)").fetchall()}
    for col in ("jazzcash_account", "easypaisa_account", "bank_account", "bank_name"):
        if col not in existing:
            cur.execute(f"ALTER TABLE isp_settings ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()
    conn.close()


def init_db():
    """Create all tables and seed default settings."""
    Base.metadata.create_all(bind=ENGINE)
    _ensure_columns()
    db = SessionLocal()
    try:
        if not db.query(ISPSettings).first():
            db.add(ISPSettings(id=1))
            db.commit()
    finally:
        db.close()


def get_db():
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def log_activity(db: Session, action: str, icon: str = "system"):
    db.add(ActivityLog(action=action, icon_type=icon))
    db.commit()
