#!/usr/bin/env python3
"""
SS Net ISP — Development Setup & Run
Run: python setup.py
"""
import subprocess
import sys
import os

def run(cmd, check=True):
    print(f"  ► {cmd}")
    return subprocess.run(cmd, shell=True, check=check).returncode == 0

def main():
    print("\n" + "═"*55)
    print("  📡  SS Net ISP — Setup")
    print("═"*55 + "\n")

    # Install dependencies
    print("📦 Installing Python requirements...")
    if not run(f"{sys.executable} -m pip install -r requirements.txt"):
        print("  ❌ pip install failed."); return

    print("\n" + "═"*55)
    print("  ✅  Setup complete!")
    print("═"*55)
    print("  Run the app:      python main.py")
    print("  Build installer:  python build.py")
    print("═"*55 + "\n")
    print("  📂  Your data is stored at:")
    home = os.path.expanduser("~")
    print(f"      {os.path.join(home, '.ssnet', 'ssnet.db')}")
    print()

    choice = input("  Launch the app now? (y/n): ").strip().lower()
    if choice == 'y':
        os.execv(sys.executable, [sys.executable, 'main.py'])

if __name__ == "__main__":
    main()
