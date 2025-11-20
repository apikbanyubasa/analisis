from dotenv import load_dotenv
import os

# --- PERBAIKAN PATH .env ---
basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(basedir, '.env')

if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    print(f"Berhasil memuat .env file dari: {dotenv_path}")
else:
    print(f"PERINGATAN: .env file tidak ditemukan di: {dotenv_path}")
    load_dotenv()
# --- AKHIR PERBAIKAN ---

import time
import threading

from app import create_app, db
from app.models import CCTV

# --- PERBAIKAN PATH IMPORT ---
# Mengimpor 'analyzer' langsung dari root folder, bukan dari 'app.admin'
from analyzer import run_detection_worker, LATEST_DETECTION_STATS
# --- AKHIR PERBAIKAN ---

# =======================================================================
# == CATATAN PENTING ==
# Kita menggunakan threading BUKAN multiprocessing.
# (Penjelasan... memory... bounding box... dll)
# =======================================================================


def start_worker_manager():
    """
    Memulai dan mengelola thread worker untuk setiap CCTV aktif.
    """
    print("Memulai Worker Manager...")
    app = create_app()

    with app.app_context():
        # 1. Ambil semua CCTV aktif dari database
        try:
            active_cctv_list = CCTV.query.filter(
                CCTV.status.ilike("aktif"),
                CCTV.stream_url.isnot(None)
            ).all()
            
            if not active_cctv_list:
                print("Tidak ada CCTV aktif yang ditemukan di database.")
                return

            print(f"Ditemukan {len(active_cctv_list)} CCTV aktif untuk dipantau.")
            
        except Exception as e:
            print(f"Gagal mengambil data CCTV dari database: {e}")
            print("Pastikan database Anda berjalan dan terkonfigurasi dengan benar di .env")
            return

        threads = []

        # 2. Luncurkan thread terpisah untuk setiap CCTV
        for cctv in active_cctv_list:
            print(f"Memulai worker untuk CCTV ID: {cctv.id} ({cctv.lokasi})")
            
            t = threading.Thread(
                target=run_detection_worker,
                args=(cctv.stream_url, cctv.lokasi, "all"),
                daemon=True
            )
            t.start()
            threads.append(t)
            
            time.sleep(1) 

        print(f"Berhasil memulai {len(threads)} worker deteksi.")
        
        # 3. Tetap jalankan skrip utama agar thread bisa terus berjalan
        try:
            while True:
                time.sleep(60) 
        except KeyboardInterrupt:
            print("\nManajer worker dihentikan (Ctrl+C). Menutup program.")


if __name__ == "__main__":
    start_worker_manager()

