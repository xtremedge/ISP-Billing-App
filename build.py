#!/usr/bin/env python3
"""
SS Net ISP build helper.

Creates:
- Windows: dist/SSNetISP/SSNetISP.exe
- macOS:   dist/SS Net ISP.app

Also generates User_Manual.pdf and places it inside build output.
"""
import os
import platform
import shutil
import subprocess
import sys
from textwrap import wrap


def run(cmd: str, check: bool = True) -> bool:
    print(f"  -> {cmd}")
    try:
        subprocess.run(cmd, shell=True, check=check)
        return True
    except subprocess.CalledProcessError:
        return False


def generate_user_manual_pdf(path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    lines = [
        "SS Net ISP - USER MANUAL",
        "",
        "1) First Launch",
        "- Open app. Use Settings to set ISP details and branding.",
        "- Add Areas and Packages before importing/adding customers.",
        "",
        "2) Customers and Billing",
        "- Add customers manually or via Import CSV.",
        "- Generate monthly bills from top toolbar (Bills).",
        "- Record payments from Billing/Unpaid pages.",
        "",
        "3) Revenue Analytics",
        "- Open Analytics page.",
        "- Choose Monthly / Last 6 Months / Yearly.",
        "- Export chart-based revenue PDF from Analytics.",
        "",
        "4) Backup and Restore",
        "- Open Import CSV page > Database Backup & Restore.",
        "- Download backup regularly and keep in cloud/USB.",
        "- Restore backup on new PC/macOS and restart app.",
        "",
        "5) Branding",
        "- Open Settings > Branding.",
        "- Upload logo and set theme colors.",
        "- Logo appears in applicable PDF reports/bills.",
        "",
        "6) Support",
        "- Database location:",
        "  Windows: C:\\Users\\<you>\\.ssnet\\ssnet.db",
        "  macOS:   /Users/<you>/.ssnet/ssnet.db",
    ]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    y = height - 48

    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, lines[0])
    y -= 26
    c.setFont("Helvetica", 10)

    for raw in lines[1:]:
        wrapped = wrap(raw, width=98) if raw else [""]
        for line in wrapped:
            if y < 48:
                c.showPage()
                y = height - 48
                c.setFont("Helvetica", 10)
            c.drawString(40, y, line)
            y -= 14

    c.showPage()
    c.save()


def main() -> None:
    os_name = platform.system()
    print("\n" + "=" * 64)
    print(f"  SS Net ISP Build ({os_name})")
    print("=" * 64 + "\n")

    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required.")
        sys.exit(1)

    print("Installing/Updating build requirements...")
    if not run(f"{sys.executable} -m pip install --upgrade pip setuptools wheel"):
        print("ERROR: pip upgrade failed.")
        sys.exit(1)
    if not run(f"{sys.executable} -m pip install -r requirements.txt pyinstaller"):
        print("ERROR: dependency install failed.")
        sys.exit(1)

    for d in ("dist", "build"):
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"  cleaned {d}/")

    print("\nBuilding with PyInstaller...")
    if not run(f"{sys.executable} -m PyInstaller ssnet.spec --clean --noconfirm"):
        print("\nERROR: build failed.")
        sys.exit(1)

    manual_paths = []
    if os_name == "Windows":
        out_dir = os.path.join("dist", "SSNetISP")
        manual_paths.append(os.path.join(out_dir, "User_Manual.pdf"))
    elif os_name == "Darwin":
        app_bundle = os.path.join("dist", "SS Net ISP.app")
        manual_paths.append(os.path.join(app_bundle, "Contents", "Resources", "User_Manual.pdf"))
        manual_paths.append(os.path.join("dist", "User_Manual.pdf"))
    else:
        out_dir = os.path.join("dist", "SSNetISP")
        manual_paths.append(os.path.join(out_dir, "User_Manual.pdf"))

    for mp in manual_paths:
        generate_user_manual_pdf(mp)
        print(f"  added manual: {mp}")

    print("\n" + "=" * 64)
    print("  Build complete")
    print("=" * 64)
    if os_name == "Windows":
        print("Output: dist/SSNetISP/SSNetISP.exe")
        print("Installer: use installer/setup.iss with Inno Setup 6+")
    elif os_name == "Darwin":
        print("Output: dist/SS Net ISP.app")
        print("Install: drag app to Applications")
    else:
        print("Output: dist/SSNetISP/")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    main()
