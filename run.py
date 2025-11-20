from dotenv import load_dotenv
import os

# --- Memuat .env ---
basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(basedir, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    print(f"Berhasil memuat .env file dari: {dotenv_path}")
else:
    load_dotenv()
# --- Akhir .env ---

import time
import threading
from app import create_app, db, socketio  # <-- Impor socketio dari app
from app.models import CCTV
from analyzer import run_detection_worker  # <-- Impor worker dari analyzer


def start_detection_threads(app_context):
    """
    Fungsi ini akan dipanggil sekali untuk memulai
    semua thread deteksi di background.
    """
    print("Memulai Worker Manager (di dalam proses Flask)...")
    with app_context:
        try:
            active_cctv_list = CCTV.query.filter(
                CCTV.status.ilike("aktif"), CCTV.stream_url.isnot(None)
            ).all()

            if not active_cctv_list:
                print("Tidak ada CCTV aktif yang ditemukan di database.")
                return

            print(f"Ditemukan {len(active_cctv_list)} CCTV aktif untuk dipantau.")

        except Exception as e:
            print(f"Gagal mengambil data CCTV dari database: {e}")
            return

        threads = []
        for cctv in active_cctv_list:
            print(f"Memulai worker thread untuk: {cctv.lokasi}")
            t = threading.Thread(
                target=run_detection_worker,
                args=(cctv.stream_url, cctv.lokasi, "all"),
                daemon=True,  # daemon=True agar thread otomatis mati saat skrip utama berhenti
            )
            t.start()
            threads.append(t)
            time.sleep(1)  # Beri jeda 1 detik antar koneksi stream

        print(f"Berhasil memulai {len(threads)} worker deteksi (thread).")


# --- INI ADALAH BAGIAN UTAMA ---

# 1. Buat aplikasi Flask
app = create_app()


# 2. Daftarkan fungsi untuk memulai worker
# Ini akan menjalankan start_detection_threads() TEPAT SEBELUM request pertama masuk
@app.before_request
def before_first_request_func():
    # Gunakan 'threading.Lock' atau atribut untuk memastikan ini HANYA berjalan sekali
    if not getattr(before_first_request_func, "has_run", False):
        # Buat app_context secara manual untuk thread
        app_context = app.app_context()
        # Jalankan worker di thread terpisah agar tidak memblokir server
        threading.Thread(target=start_detection_threads, args=(app_context,)).start()
        before_first_request_func.has_run = True
        print("Fungsi before_first_request telah dijadwalkan.")


# 3. Jalankan server
if __name__ == "__main__":
    print("Menjalankan server Flask-SocketIO...")
    # --- PERUBAHAN FINAL ---
    # debug=False SANGAT PENTING agar worker threading berjalan dengan benar.
    # Mode Debug (reloader) membuat dua proses, memisahkan memori worker & server.
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
