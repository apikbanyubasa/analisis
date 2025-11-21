import time
import threading
from app.models import CCTV
from analyzer import run_detection_worker
from app import db
from sqlalchemy import select
from sqlalchemy.orm import undefer_group  # Import untuk memaksa eager load

# Global State
ACTIVE_WORKER_THREADS = {}
WORKER_KILL_SWITCH = {}


def stop_all_detection_threads():
    global ACTIVE_WORKER_THREADS, WORKER_KILL_SWITCH

    print(f"[WorkerManager] Mengirim sinyal berhenti ke {len(WORKER_KILL_SWITCH)} worker...")
    for location, event in WORKER_KILL_SWITCH.items():
        event.set() # Kirim sinyal berhenti
        print(f"[WorkerManager] Sinyal STOP dikirim ke: {location}")

    # --- PERBAIKAN KRITIS: BERI WAKTU AGAR THREAD LAMA MATI ---
    # Beri jeda 2-3 detik agar worker lama sempat keluar dari loop dan menjalankan cap.release()
    time.sleep(3) 
    print("[WorkerManager] Jeda 3 detik selesai. Membersihkan state...")
    # --- AKHIR PERBAIKAN KRITIS ---

    ACTIVE_WORKER_THREADS = {}
    WORKER_KILL_SWITCH = {}
    print("[WorkerManager] Semua state worker lama dibersihkan.")


def initialize_workers_and_server(app):
    """
    Fungsi master untuk menghentikan, memuat data CCTV baru secara eager,
    dan memulai thread deteksi baru.
    """

    stop_all_detection_threads()

    # 1. Buat Application Context dan Ambil data CCTV
    active_cctv_list = []
    with app.app_context():
        try:
            # --- SOLUSI DETACHED INSTANCE ERROR ---
            # Menggunakan undefer_group('*') untuk memastikan SEMUA kolom dimuat segera
            stmt = (
                select(CCTV)
                .filter(CCTV.status.ilike("aktif"), CCTV.stream_url.isnot(None))
                .options(undefer_group("*"))
            )  # WAJIB!

            # Gunakan .scalars().all() untuk memuat sepenuhnya sebelum keluar context
            active_cctv_list = db.session.scalars(stmt).all()

            # Tidak perlu db.session.commit() setelah SELECT.

        except Exception as e:
            # Rollback wajib jika ada error saat query
            db.session.rollback()
            print(f"[WorkerManager] GAGAL KRITIS memuat daftar CCTV dari DB: {e}")
            return  # Gagal, tidak ada worker yang dimulai

    # 2. Logika memulai thread worker (DI LUAR konteks DB)

    if not active_cctv_list:
        print("[WorkerManager] Tidak ada CCTV aktif untuk dipantau.")
        return

    print(
        f"[WorkerManager] Ditemukan {len(active_cctv_list)} CCTV aktif untuk dipantau."
    )

    threads = []
    for cctv in active_cctv_list:
        worker_stop_event = threading.Event()

        # cctv.lokasi diakses di sini, dan sekarang datanya sudah dimuat penuh
        WORKER_KILL_SWITCH[cctv.lokasi] = worker_stop_event

        print(f"[WorkerManager] Memulai worker thread untuk: {cctv.lokasi}")
        t = threading.Thread(
            target=run_detection_worker,
            args=(cctv.stream_url, cctv.lokasi, "all", worker_stop_event),
            daemon=True,
        )
        t.start()
        threads.append(t)
        ACTIVE_WORKER_THREADS[cctv.lokasi] = t
        time.sleep(0.5)

    print(f"[WorkerManager] Berhasil memulai {len(threads)} worker deteksi.")


def reload_workers_thread(app):
    """Dipanggil dari route admin untuk reload worker."""
    print("Memicu reload worker thread...")
    # Jalankan initialize_workers_and_server di thread terpisah
    threading.Thread(
        target=initialize_workers_and_server, args=(app,), daemon=True
    ).start()
