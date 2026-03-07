"""
Runs FastAPI + uvicorn in a background thread inside the desktop app.
The PyQt6 window loads localhost:8765 in its WebEngine view.
"""
import threading
import uvicorn
from app.api.routes import app

PORT = 8765
_server_thread = None


def start_server():
    """Start the embedded API server in a daemon thread."""
    global _server_thread
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="warning",
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    _server_thread = threading.Thread(target=server.run, daemon=True)
    _server_thread.start()


def get_base_url():
    return f"http://127.0.0.1:{PORT}"
