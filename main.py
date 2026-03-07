"""
SS Net ISP Billing — Desktop Application Entry Point
PyQt6 window embedding a WebEngine that loads the FastAPI + HTML UI.
"""
import warnings
# Suppress requests/urllib3 dependency warnings before imports
try:
    import urllib3
    warnings.simplefilter('ignore', urllib3.exceptions.DependencyWarning)
except (ImportError, AttributeError):
    pass
try:
    from requests.packages.urllib3.exceptions import DependencyWarning
    warnings.simplefilter('ignore', DependencyWarning)
except (ImportError, AttributeError):
    pass

import os
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-features=Translate,OptimizationHints "
    "--disable-extensions "
    "--disable-background-networking "
    "--no-first-run"
)

import sys
import time
import threading
import requests
import shutil

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QSplashScreen, QLabel, QProgressBar, QMessageBox, QFileDialog
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile, QWebEnginePage
from PyQt6.QtCore import QUrl, Qt, QTimer, pyqtSignal, QObject, QSize
from PyQt6.QtGui import QPixmap, QColor, QPainter, QFont, QIcon

from app.api.server import start_server, PORT


# ─── CUSTOM PAGE (allow opening wa.me links in default browser) ───────────────
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices

class SSNetPage(QWebEnginePage):
    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame):
        href = url.toString()
        # Open external links (WhatsApp, SMS, mailto) in system browser
        if href.startswith("https://wa.me") or href.startswith("sms:") or href.startswith("mailto:"):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


# ─── SPLASH SCREEN ───────────────────────────────────────────────────────────
class SplashScreen(QSplashScreen):
    def __init__(self, base_path):
        # Create a gradient splash pixmap
        pix = QPixmap(520, 300)
        pix.fill(QColor("#0a0e1a"))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background glow
        painter.setBrush(QColor(0, 212, 255, 18))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(20, 20, 200, 200)

        # Logo box
        painter.setBrush(QColor(0, 100, 200))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(210, 60, 100, 100, 20, 20)

        # ISP icon text
        painter.setPen(QColor("#ffffff"))
        f = QFont("Arial", 42)
        painter.setFont(f)
        painter.drawText(225, 130, "📡")

        # App name
        painter.setPen(QColor("#00d4ff"))
        f2 = QFont("Arial", 26, QFont.Weight.Bold)
        painter.setFont(f2)
        painter.drawText(0, 200, 520, 40, Qt.AlignmentFlag.AlignCenter, "SS NET ISP")

        # Subtitle
        painter.setPen(QColor("#8892a4"))
        f3 = QFont("Arial", 11)
        painter.setFont(f3)
        painter.drawText(0, 235, 520, 30, Qt.AlignmentFlag.AlignCenter, "Billing Management System")

        # Branding
        dev_logo_path = os.path.join(base_path, "app", "static", "developer_logo.png")
        if os.path.exists(dev_logo_path):
            logo_pix = QPixmap(dev_logo_path)
            logo_pix = logo_pix.scaledToHeight(30, Qt.TransformationMode.SmoothTransformation)
            x_pos = (520 - logo_pix.width()) // 2
            painter.drawPixmap(x_pos, 280, logo_pix)
        else:
            painter.setPen(QColor("#556073"))
            f4 = QFont("Arial", 9)
            painter.setFont(f4)
            painter.drawText(0, 280, 520, 20, Qt.AlignmentFlag.AlignCenter, "Developed by SS Net")

        painter.end()
        super().__init__(pix)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)

        self._progress = QProgressBar(self)
        self._progress.setGeometry(60, 270, 400, 8)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar { background:#1a2236; border-radius:4px; border:none; }
            QProgressBar::chunk { background:#00d4ff; border-radius:4px; }
        """)

        self._label = QLabel("Starting server...", self)
        self._label.setGeometry(0, 252, 520, 20)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color:#8892a4; font-size:11px;")

    def set_progress(self, value: int, msg: str = ""):
        self._progress.setValue(value)
        if msg:
            self._label.setText(msg)
        QApplication.processEvents()


# ─── MAIN WINDOW ─────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SS Net ISP Billing")
        self.setMinimumSize(1200, 760)
        self.resize(1440, 900)

        # Centre on screen
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width()  - 1440) // 2
        y = (screen.height() -  900) // 2
        self.move(x, y)

        # WebEngine
        self.profile = QWebEngineProfile("ssnet", self)
        self.page    = SSNetPage(self.profile, self)
        self.browser = QWebEngineView(self)
        self.browser.setPage(self.page)

        # Settings
        s = self.page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled,          True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled,         True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,    True)

        self.setCentralWidget(self.browser)

    def closeEvent(self, event):
        # Explicitly clean up WebEngine items to avoid the "page still not deleted" warning
        self.browser.setPage(None)
        self.page.deleteLater()
        self.profile.deleteLater()
        super().closeEvent(event)

    def load_app(self):
        url = QUrl(f"http://127.0.0.1:{PORT}/")
        self.browser.setUrl(url)


# ─── SERVER READY CHECK ──────────────────────────────────────────────────────
def wait_for_server(timeout=10) -> bool:
    """Poll until FastAPI is responding."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"http://127.0.0.1:{PORT}/api/settings", timeout=0.8)
            if r.status_code in (200, 422):
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


# ─── APPLICATION BOOTSTRAP ───────────────────────────────────────────────────
def main():
    # Base path for resources (PyInstaller support)
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

    # High-DPI
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("SS Net ISP")
    app.setOrganizationName("SS Net")

    # DB First-Run Setup
    home = os.path.expanduser("~")
    data_dir = os.path.join(home, ".ssnet")
    db_path = os.path.join(data_dir, "ssnet.db")

    if not os.path.exists(db_path):
        os.makedirs(data_dir, exist_ok=True)
        msgBox = QMessageBox()
        msgBox.setWindowTitle("Welcome to SS Net ISP")
        msgBox.setText("No existing database found.\nWould you like to start a fresh database or restore from a backup?")
        
        btn_fresh = msgBox.addButton("Start Fresh", QMessageBox.ButtonRole.AcceptRole)
        btn_restore = msgBox.addButton("Restore Backup", QMessageBox.ButtonRole.ActionRole)
        msgBox.exec()
        
        if msgBox.clickedButton() == btn_restore:
            file_name, _ = QFileDialog.getOpenFileName(
                None, "Select Backup Database", "", "SQLite DB (*.db);;All Files (*)"
            )
            if file_name and os.path.exists(file_name):
                shutil.copy2(file_name, db_path)

    # Splash
    splash = SplashScreen(base_path)
    splash.show()
    splash.set_progress(5, "Initializing database...")
    QApplication.processEvents()

    # Start embedded FastAPI server
    splash.set_progress(20, "Starting API server...")
    start_server()

    # Wait until server is ready
    splash.set_progress(40, "Waiting for server...")
    for i in range(40, 85):
        try:
            r = requests.get(f"http://127.0.0.1:{PORT}/api/settings", timeout=0.5)
            if r.status_code in (200, 422, 404):
                break
        except Exception:
            pass
        time.sleep(0.08)
        splash.set_progress(i, "Starting API server...")

    splash.set_progress(90, "Loading application...")

    # Main window
    window = MainWindow()
    window.load_app()

    splash.set_progress(100, "Ready!")
    time.sleep(0.4)
    splash.finish(window)
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
