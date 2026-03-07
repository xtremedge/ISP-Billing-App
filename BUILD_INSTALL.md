# Build and Install Guide (Windows + macOS)

This guide is designed for repeatable builds with minimal errors.

## 1. Prerequisites

- Python 3.10 to 3.13
- `pip` available in terminal
- Git (optional, if cloning)

### Windows only

- Microsoft Visual C++ Redistributable (latest)
- Inno Setup 6+ (only if you want `.exe` installer wizard)

### macOS only

- Xcode Command Line Tools:
  - `xcode-select --install`
- For public distribution (outside your own machine):
  - Apple Developer account for code-sign + notarization

## 2. Clean Environment (Recommended)

```bash
python -m venv .venv
```

### Activate

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

macOS:

```bash
source .venv/bin/activate
```

## 3. Install Dependencies

```bash
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

## 4. Build App

```bash
python build.py
```

What this does:

- Installs/updates build dependencies
- Cleans old `dist/` and `build/`
- Builds with PyInstaller using `netpulse.spec`
- Generates `User_Manual.pdf` and adds it to build output

## 5. Build Outputs

### Windows

- App: `dist/NetPulseISP/NetPulseISP.exe`
- User manual: `dist/NetPulseISP/User_Manual.pdf`

Optional installer (wizard):

- Open `installer/setup.iss` in Inno Setup
- Build installer

### macOS

- App: `dist/NetPulse ISP.app`
- User manual:
  - `dist/NetPulse ISP.app/Contents/Resources/User_Manual.pdf`
  - `dist/User_Manual.pdf`

Install by dragging app to `Applications`.

## 6. First Run / Install Notes

- App stores data in user profile:
  - Windows: `C:\Users\<you>\.netpulse\netpulse.db`
  - macOS: `/Users/<you>/.netpulse/netpulse.db`
- Reinstalling app does not remove this DB unless you delete it manually.

## 7. Troubleshooting

### Build fails at PyInstaller step

- Confirm Python version:
  - `python --version`
- Reinstall deps:
  - `pip install --upgrade pip setuptools wheel`
  - `pip install -r requirements.txt pyinstaller`

### Windows SmartScreen warning

- Expected for unsigned apps.
- For distribution, sign executable with a code-signing certificate.

### macOS “App is damaged” or blocked

- For local use: right-click app -> Open
- For public distribution: codesign + notarize with Apple Developer account

### Missing WebEngine components

- Ensure `PyQt6-WebEngine` is installed:
  - `pip install PyQt6-WebEngine`

## 8. QA Checklist Before Shipping

- Launch app and verify dashboard loads
- Create customer, generate bill, mark payment
- Verify Analytics page and PDF export
- Verify logo upload and theme colors
- Download and restore backup
- Confirm User Manual PDF exists in build output
