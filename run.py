from dotenv import load_dotenv
import os

basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(basedir, ".env")
if os.path.exists(dotenv_path):

    load_dotenv(dotenv_path=dotenv_path, override=True)
    print(f"Berhasil memuat .env file dari: {dotenv_path}")
else:
    load_dotenv(override=True)

import time
import threading
from app import create_app, db, socketio
from app.models import CCTV

# Mengimpor fungsi set_global_app_instance dari analyzer
from analyzer import run_detection_worker, set_global_app_instance

# Mengimpor fungsi management dari workers_manager.py
from workers_manager import initialize_workers_and_server, reload_workers_thread


app = create_app()

# 💥 BARIS KRITIS: INJEKSI INSTANCE APLIKASI SENTRAL KE MODUL ANALYZER
# Ini harus dilakukan sebelum thread worker dimulai!
set_global_app_instance(app)

if __name__ == "__main__":

    print("Menjalankan server Flask-SocketIO...")

    @socketio.on("connect")
    def handle_connect():
        if not getattr(handle_connect, "has_initialized", False):
            # initialize_workers_and_server akan menggunakan APP instance yang sudah diinjeksi
            threading.Thread(
                target=initialize_workers_and_server, args=(app,), daemon=True
            ).start()
            handle_connect.has_initialized = True

    socketio.run(
        app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True
    )
