#!/usr/bin/env python3
"""
SS Net ISP — Build Script
Creates a standalone installable .exe (Windows) or .app (macOS).

Usage:
    python build.py
"""
import subprocess
import sys
import os
import shutil
import platform

def run(cmd, check=True):
    print(f"  ► {cmd}")
    return subprocess.run(cmd, shell=True, check=check).returncode == 0

def main():
    OS = platform.system()
    print("\n" + "═"*60)
    print(f"  📡  SS Net ISP — Build ({OS})")
    print("═"*60 + "\n")

    # 1. Install requirements
    print("📦 Installing build requirements...")
    run(f"{sys.executable} -m pip install -r requirements.txt pyinstaller")

    # 2. Clean previous build
    for d in ["dist", "build"]:
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"  🗑️  Cleaned {d}/")

    # 3. Build with PyInstaller
    print("\n🔨 Building with PyInstaller...")
    ok = run(f"{sys.executable} -m PyInstaller netpulse.spec --clean --noconfirm")

    if not ok:
        print("\n❌ Build failed!")
        return

    # 4. Report output
    print("\n" + "═"*60)
    print("  ✅  Build Complete!")
    print("═"*60)

    if OS == "Windows":
        exe = os.path.join("dist", "NetPulseISP", "NetPulseISP.exe")
        print(f"  📁  Output: {exe}")
        print("  💡  You can also create an installer using Inno Setup.")
        print("      See installer/setup.iss for the Inno Setup script.")

    elif OS == "Darwin":
        app = os.path.join("dist", "NetPulse ISP.app")
        print(f"  📁  Output: {app}")
        print("  💡  Drag to Applications folder to install.")
        print("      For distribution, sign with: codesign -s 'Developer ID' dist/NetPulse\\ ISP.app")

    else:
        print(f"  📁  Output: dist/NetPulseISP/")

    print("═"*60 + "\n")


if __name__ == "__main__":
    main()
