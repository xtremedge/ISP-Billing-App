# 📡 SS Net ISP Billing — Desktop Application

A fully self-contained cross-platform desktop app for ISP billing management.
**No internet required. No server setup. No MySQL. Just run and use.**

Built with: **PyQt6** (desktop window) + **FastAPI** (embedded API) + **SQLite** (local database)

---

## ✅ Features

| Feature | Detail |
|---|---|
| Customer Management | Import via CSV, area-wise filtering, per-customer history |
| Monthly Billing | Auto-generate bills for all active customers |
| Extra Charges | Modem, charger, reconnection fee, etc. |
| Payment Tracking | Partial & full payments with full history |
| PDF Bills | Printable invoices with PAID/UNPAID/OVERDUE stamp |
| WhatsApp / SMS | One-click reminders open in your browser/SMS app |
| Overdue Alerts | Popup on startup if overdue bills exist |
| Embedded Database | SQLite — no MySQL, no server config needed |
| Cross Platform | Windows & macOS (same codebase) |

---

## ⚡ Quick Start (Development)

### Requirements
- Python 3.10+
- pip

### 1. Install & Run

```bash
# Clone / extract the project folder
cd netpulse_desktop

# Install all requirements and optionally launch
python setup.py
```

Or manually:
```bash
pip install -r requirements.txt
python main.py
```

The app opens a native desktop window — no browser needed.

---

## 📦 Build Installable App

### Windows → .exe installer

```bash
# Step 1: Build with PyInstaller
python build.py

# Step 2: Create installer (optional)
# Open installer/setup.iss in Inno Setup 6+
# Click Build → creates dist/installer/NetPulseISP_Setup_v1.0.0.exe
```

### macOS → .app bundle

```bash
python build.py
# Output: dist/NetPulse ISP.app
# Drag to Applications to install
```

---

## 📂 Data Storage

Your data (SQLite database) is stored at:

| Platform | Path |
|---|---|
| Windows | `C:\Users\<you>\.netpulse\netpulse.db` |
| macOS | `/Users/<you>/.netpulse/netpulse.db` |

Data is **never deleted** when you update or reinstall the app.

---

## 📂 CSV Import Format

```
Sr.no, username, full name, mobile, expiring, package, service 1, service 2, service 3, service 4
```

- **service 1** = Area (e.g. "Block A", "Sector 5")
- **expiring** = `YYYY-MM-DD` format
- **mobile** = `03001234567` or `+923001234567`

---

## 🏗️ Project Structure

```
netpulse_desktop/
├── main.py                   ← PyQt6 window (app entry point)
├── setup.py                  ← Dev setup / run helper
├── build.py                  ← Build installer
├── netpulse.spec             ← PyInstaller config
├── requirements.txt
├── app/
│   ├── api/
│   │   ├── routes.py         ← FastAPI endpoints (all 25+ routes)
│   │   └── server.py         ← Embedded uvicorn server thread
│   ├── db/
│   │   └── models.py         ← SQLAlchemy models (SQLite)
│   └── static/
│       └── index.html        ← Full UI (dark theme, all 10 pages)
├── installer/
│   └── setup.iss             ← Inno Setup script (Windows)
└── resources/
    └── (icon.ico / icon.icns for branding)
```

---

## 🔧 How It Works

```
┌────────────────────────────────────────┐
│          PyQt6 Desktop Window           │
│  ┌──────────────────────────────────┐  │
│  │     QWebEngineView (Chrome)      │  │
│  │  loads http://127.0.0.1:8765/    │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │  FastAPI + uvicorn (background)  │  │
│  │  Port 8765  ←→  SQLite DB        │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
```

1. App starts → PyInstaller bundle launches `main.py`
2. `main.py` starts FastAPI server on localhost:8765 in a background thread
3. PyQt6 WebEngine loads `http://127.0.0.1:8765/`
4. The HTML/JS UI talks to `/api/...` endpoints
5. FastAPI reads/writes SQLite — all local, no internet needed
6. WhatsApp/SMS links open in your default system browser

---

## 📞 API Reference (for developers)

All API runs on `http://127.0.0.1:8765/api/`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/dashboard` | Stats, areas, packages, activity |
| GET/POST | `/customers` | List / create customers |
| GET | `/customers/{id}` | Customer detail with bills |
| POST | `/import-csv` | Bulk CSV import |
| GET/POST | `/bills` | Bills list / create |
| POST | `/generate-bills` | Auto-generate monthly bills |
| POST | `/payments` | Record a payment |
| POST | `/charges` | Add extra charge |
| GET | `/bills/{id}/pdf` | Download PDF invoice |
| GET | `/customers-due-soon` | Expiry reminder list |
| GET/PATCH | `/settings` | ISP settings |
| GET/POST | `/areas` | Manage service areas |
| GET/POST | `/packages` | Manage packages |
