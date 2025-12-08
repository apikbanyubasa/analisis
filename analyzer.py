import cv2
import numpy as np
import easyocr
from datetime import datetime, timedelta
import time
import threading
from ultralytics import YOLO
import os
from collections import defaultdict, deque
import math
import re

# --- Impor dari Flask dan Model untuk akses DB terpusat ---
from app import create_app, db

# 💡 Tambahkan CCTV ke import untuk konfigurasi dinamis
from app.models import (
    CountingData,
    ParkingViolation,
    CrowdDetection,
    OdolDetection,
    CCTV,
)
from flask import (
    Flask,
    # current_app tidak diperlukan
)

# --- Impor SocketIO dari Server ---
try:
    from app import socketio

    print(
        "Berhasil mengimpor SocketIO Server (app.socketio) untuk notifikasi real-time."
    )
except ImportError:
    print(
        "PERINGATAN: Gagal mengimpor SocketIO. Notifikasi real-time tidak akan berfungsi."
    )

    class DummySocketIO:
        def emit(self, *args, **kwargs):
            pass

    socketio = DummySocketIO()
# --- AKHIR IMPOR ---

# Global untuk menyimpan instance aplikasi Flask SENTRAL
GLOBAL_APP_INSTANCE = None
# Global Lock untuk mengontrol akses konkuren ke fungsi cleanup
DB_CLEANUP_LOCK = threading.Lock()
# --- VARIABEL GLOBAL UNTUK KONTROL NOTIFIKASI ---
GLOBAL_NOTIFICATION_ENABLED = True


def set_global_app_instance(app_instance: Flask):
    """Menginjeksi instance aplikasi Flask sentral ke modul ini."""
    global GLOBAL_APP_INSTANCE
    GLOBAL_APP_INSTANCE = app_instance
    print(f"[{threading.current_thread().name}] Global Flask App instance set.")


def get_flask_app_context():
    """Mendapatkan konteks aplikasi sentral yang diinisialisasi di run.py."""
    global GLOBAL_APP_INSTANCE
    if GLOBAL_APP_INSTANCE is None:
        raise RuntimeError(
            "Flask app instance belum diinisialisasi. Panggil set_global_app_instance() dari thread utama."
        )
    return GLOBAL_APP_INSTANCE.app_context()


# Load YOLO model
try:
    model = YOLO("models/yolov8n.pt")
    print("YOLO model loaded successfully")
except Exception as e:
    print(f"Error loading YOLO model: {e}")
    model = None

# 2. [BARU] Load Model Deteksi Plat (Punya Kamu)
try:
    plate_model = YOLO("models/best.pt")
    print("✅ YOLO Plate Model loaded successfully")
except Exception as e:
    print(f"❌ Error loading YOLO Plate model: {e}")
    plate_model = None

# Load EasyOCR Reader
try:
    # gpu=True jika pakai NVIDIA, gpu=False jika CPU
    READER_OCR = easyocr.Reader(["id", "en"], gpu=False)
    print("✅ EasyOCR reader siap.")
except Exception as e:
    print(f"❌ Error loading EasyOCR: {e}")
    READER_OCR = None


# Konfigurasi deteksi
DETECTION_CLASSES = {
    0: "person",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}
COLORS = {
    "person": (30, 144, 255),
    "car": (30, 255, 144),
    "motorcycle": (255, 255, 30),
    "bus": (144, 30, 255),
    "truck": (255, 165, 0),
    "crowd": (0, 0, 255),
    "parking": (255, 0, 0),
    "odol": (0, 255, 255),
}

# --- GARIS KOORDINAT DEFAULT (FALLBACK JEMBATAN MERAH) ---
DEFAULT_LINE_1_Y = 198
DEFAULT_LINE_2_Y = 244
DEFAULT_REAL_DISTANCE = 3.5

# Variabel global untuk zona (tetap)
ZONE_JAUH = 1
ZONE_TENGAH = 2
ZONE_DEKAT = 3

# 💡 VAR BARU: Global untuk menyimpan data kecepatan per lokasi (Fix Race Condition)
GLOBAL_TRACKED_OBJECTS = {}
LOCATION_TRACKERS = {}
LATEST_DETECTION_STATS = {}


def get_point_side(point_x, point_y, line_x1, line_y1, line_x2, line_y2):
    return (point_x - line_x1) * (line_y2 - line_y1) - (point_y - line_y1) * (
        line_x2 - line_x1
    )


class SimpleObjectTracker:
    # --- Metode Class disingkat, diasumsikan tidak diubah ---
    def __init__(
        self, max_disappeared=30, max_distance=50, initial_counts=None, fps=20
    ):
        self.next_object_id = 0
        self.objects = {}
        self.disappeared = defaultdict(int)
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance
        self.object_zone = defaultdict(int)
        self.counts_menuju_jauh = defaultdict(int)
        self.counts_menuju_dekat = defaultdict(int)
        self.first_seen = defaultdict(float)
        self.counted = defaultdict(bool)
        self.last_moved = defaultdict(float)
        self.is_parked = defaultdict(bool)
        self.is_odol_logged = defaultdict(bool)
        self.plate_logged = defaultdict(bool)
        self.history = defaultdict(lambda: deque(maxlen=10))
        self.fps = fps
        self.speed_history = defaultdict(lambda: deque(maxlen=5))

        # --- Bagian yang berkonflik di __init__ ---
        src_pts = np.array(
            [[370, 253], [463, 251], [480, 350], [373, 350]], dtype=np.float32
        )
        self.perspective_matrix = None
        self.ppm_birdseye = None
        # --- Akhir bagian konflik di __init__ ---

    def reset_completely(self):
        print(f"[{threading.current_thread().name}] RESETTING TRACKER COMPLETELY")
        self.next_object_id = 0
        self.objects.clear()
        self.disappeared.clear()
        self.history.clear()
        self.speed_history.clear()
        self.plate_logged.clear()
        self.is_parked.clear()
        self.is_odol_logged.clear()
        self.last_moved.clear()
        self.first_seen.clear()
        self.counted.clear()
        self.object_zone.clear()
        self.counts_menuju_jauh.clear()
        self.counts_menuju_dekat.clear()
        print(f"[{threading.current_thread().name}] Tracker reset done.")

    def calculate_distance(self, point1, point2):
        return math.sqrt((point1[0] - point2[0]) ** 2 + (point1[1] - point2[1]) ** 2)

    def register(self, detection):
        class_name = detection["class_name"]
        obj_id = self.next_object_id
        self.objects[obj_id] = {
            "center": detection["center"],
            "bbox": detection["bbox"],
            "class_name": class_name,
            "class_id": detection["class_id"],
            "confidence": detection["confidence"],
            "counted": False,
            "prev_center": detection["center"],
        }
        self.disappeared[obj_id] = 0
        current_time = time.time()
        self.first_seen[obj_id] = current_time
        self.counted[obj_id] = False
        self.last_moved[obj_id] = current_time
        self.is_parked[obj_id] = False
        self.is_odol_logged[obj_id] = False
        self.plate_logged[obj_id] = False
        self.next_object_id += 1

    def deregister(self, object_id):
        for d in [
            self.objects,
            self.disappeared,
            self.history,
            self.speed_history,
            self.is_odol_logged,
            self.plate_logged,
            self.is_parked,
            self.last_moved,
            self.first_seen,
            self.counted,
        ]:
            d.pop(object_id, None)

    # 💡 PERBAIKAN: Terima config dan local_speed_data (pengganti TRACKED global)
    def update(self, detections, config, local_speed_data):
        # Dapatkan konfigurasi garis & jarak dari dictionary config
        line1_y = config["line1_y"]
        line2_y = config["line2_y"]
        pixel_distance = config["pixel_distance"]
        real_distance_m = config["real_distance_m"]

        if len(detections) == 0:
            for object_id in list(self.disappeared.keys()):
                self.disappeared[object_id] += 1
                if self.disappeared[object_id] > self.max_disappeared:
                    self.deregister(object_id)
                    local_speed_data.pop(
                        object_id, None
                    )  # 💡 Hapus dari data kecepatan lokal
            return self.objects

        if len(self.objects) == 0:
            for detection in detections:
                self.register(detection)
            return self.objects
        else:
            object_ids = list(self.objects.keys())
            used_detection_indices = set()
            used_object_ids = set()
            for detection_idx, detection in enumerate(detections):
                min_distance = float("inf")
                min_object_id = None
                for object_id in object_ids:
                    if object_id in used_object_ids:
                        continue
                    distance = self.calculate_distance(
                        detection["center"], self.objects[object_id]["center"]
                    )
                    if distance < min_distance and distance < self.max_distance:
                        min_distance = distance
                        min_object_id = object_id
                if min_object_id is not None:
                    self.objects[min_object_id]["center"] = detection["center"]
                    self.objects[min_object_id]["bbox"] = detection["bbox"]
                    self.objects[min_object_id]["confidence"] = detection["confidence"]
                    self.disappeared[min_object_id] = 0

                    obj_id = min_object_id
                    center_x, center_y = self.objects[obj_id]["center"]
                    class_name = self.objects[obj_id]["class_name"]

                    self.history[obj_id].append((center_x, center_y, time.time()))

                    # 💡 LOGIKA ZONA MENGGUNAKAN CONFIG DINAMIS
                    l1_x1, l1_y1, l1_x2, l1_y2 = 0, line1_y, 600, line1_y
                    l2_x1, l2_y1, l2_x2, l2_y2 = 0, line2_y, 600, line2_y

                    side_1 = get_point_side(
                        center_x, center_y, l1_x1, l1_y1, l1_x2, l1_y2
                    )
                    side_2 = get_point_side(
                        center_x, center_y, l2_x1, l2_y1, l2_x2, l2_y2
                    )

                    current_zone = 0
                    if side_1 > 0:
                        current_zone = ZONE_JAUH
                    elif side_1 < 0 and side_2 > 0:
                        current_zone = ZONE_TENGAH
                    elif side_2 < 0:
                        current_zone = ZONE_DEKAT

                    previous_zone = self.object_zone[obj_id]

                    if current_zone != 0 and previous_zone != current_zone:

                        if previous_zone == ZONE_DEKAT and current_zone == ZONE_TENGAH:
                            self.object_zone[obj_id] = ZONE_TENGAH
                        elif previous_zone == ZONE_TENGAH and current_zone == ZONE_JAUH:
                            self.counts_menuju_jauh[class_name] += 1
                            self.object_zone[obj_id] = ZONE_JAUH
                            print(
                                f"HITUNGAN (MENUJU JAUH) {class_name}: {self.counts_menuju_jauh[class_name]}"
                            )

                        elif previous_zone == ZONE_JAUH and current_zone == ZONE_TENGAH:
                            self.object_zone[obj_id] = ZONE_TENGAH
                        elif (
                            previous_zone == ZONE_TENGAH and current_zone == ZONE_DEKAT
                        ):
                            self.counts_menuju_dekat[class_name] += 1
                            self.object_zone[obj_id] = ZONE_DEKAT
                            print(
                                f"HITUNGAN (MENUJU DEKAT) {class_name}: {self.counts_menuju_dekat[class_name]}"
                            )

                        elif previous_zone == 0:
                            self.object_zone[obj_id] = current_zone

                    # --- KECEPATAN: Gunakan local_speed_data (HINDARI RACE CONDITION) ---
                    obj_id = min_object_id
                    center_y = self.objects[obj_id]["center"][1]

                    if class_name in ["car", "motorcycle", "bus", "truck"]:

                        # 💡 Gunakan local_speed_data
                        if obj_id not in local_speed_data:
                            local_speed_data[obj_id] = {
                                "cx": center_x,
                                "cy": center_y,
                                "last_y": center_y,
                                "timestamp": time.time(),
                                "speed": 0.0,
                            }

                        else:
                            # 💡 Gunakan local_speed_data
                            dy = abs(center_y - local_speed_data[obj_id]["last_y"])

                            if dy > 5:
                                dt = time.time() - local_speed_data[obj_id]["timestamp"]

                                if (
                                    dt > 0 and pixel_distance > 0
                                ):  # 💡 Gunakan pixel_distance dinamis
                                    dist_meters = (
                                        dy / pixel_distance
                                    ) * real_distance_m  # 💡 Gunakan real_distance_m dinamis
                                    speed_kmh = (dist_meters / dt) * 3.6
                                    local_speed_data[obj_id]["speed"] = speed_kmh

                                # update posisi terakhir
                                local_speed_data[obj_id]["cx"] = center_x
                                local_speed_data[obj_id]["cy"] = center_y
                                local_speed_data[obj_id]["last_y"] = center_y
                                local_speed_data[obj_id]["timestamp"] = time.time()
                    # --- AKHIR KECEPATAN DARI REMOTE ---

                    used_detection_indices.add(detection_idx)
                    used_object_ids.add(min_object_id)

            for detection_idx, detection in enumerate(detections):
                if detection_idx not in used_detection_indices:
                    self.register(detection)

            current_time = time.time()
            for object_id in object_ids:
                if object_id not in used_object_ids:
                    self.disappeared[object_id] += 1
                    if self.disappeared[object_id] > self.max_disappeared:
                        self.deregister(object_id)
                        local_speed_data.pop(
                            object_id, None
                        )  # 💡 Hapus dari data kecepatan lokal

                else:
                    current_center = self.objects[object_id]["center"]
                    prev_center = self.objects[object_id].get(
                        "prev_center", current_center
                    )
                    move_dist = self.calculate_distance(current_center, prev_center)
                    if move_dist > 5:
                        self.last_moved[object_id] = current_time
                        self.is_parked[object_id] = False
                        self.is_odol_logged[object_id] = False
                        self.plate_logged[object_id] = False
                    self.objects[object_id]["prev_center"] = current_center
            return self.objects

    def transform_point(self, point):
        # transform_point tidak akan digunakan lagi dengan speed calculation baru
        if self.perspective_matrix is None:
            return point
        point_np = np.array([[[point[0], point[1]]]], dtype=np.float32)
        transformed_point = cv2.perspectiveTransform(point_np, self.perspective_matrix)
        return (transformed_point[0][0][0], transformed_point[0][0][1])


# -------------------------------------------------------------------
# == FUNGSI DATABASE: KOREKSI PENGGUNAAN ID (FK) ==
# -------------------------------------------------------------------


def get_cctv_id_by_location_name(location_name):
    """Mencari ID CCTV berdasarkan nama lokasi (lokasi) dari database."""
    # Fungsi ini diasumsikan dipanggil dalam konteks aplikasi Flask!
    try:
        # Asumsi kolom lokasi di CCTV adalah 'lokasi'
        cctv_obj = CCTV.query.filter_by(lokasi=location_name).first()
        if cctv_obj:
            return cctv_obj.id
        else:
            print(
                f"[{threading.current_thread().name}] ERROR: CCTV dengan lokasi '{location_name}' tidak ditemukan. Tidak bisa menyimpan data."
            )
            return None
    except Exception as e:
        print(f"[{threading.current_thread().name}] DB lookup error (get_cctv_id): {e}")
        return None


def cleanup_old_data(
    model, cctv_id, max_rows=50
):  # 💡 Menerima cctv_id, bukan location_name
    """
    Membersihkan baris data lama dari suatu model (tabel)
    untuk lokasi tertentu (berdasarkan cctv_id) jika jumlah total baris melebihi batas.
    """
    with DB_CLEANUP_LOCK:
        try:
            # 💡 FILTER berdasarkan cctv_id
            current_row_count = model.query.filter_by(cctv_id=cctv_id).count()

            if current_row_count > max_rows:
                rows_to_delete = current_row_count - max_rows

                oldest_ids = (
                    db.session.query(model.id)
                    .filter_by(cctv_id=cctv_id)  # 💡 Filter berdasarkan cctv_id
                    .order_by(model.id.asc())
                    .limit(rows_to_delete)
                    .subquery()
                )

                model.query.filter(model.id.in_(oldest_ids)).delete(
                    synchronize_session=False
                )

                db.session.commit()
                thread_name = threading.current_thread().name
                print(
                    f"[{thread_name}] CLEANUP SUCCESS: Dihapus {rows_to_delete} baris lama dari {model.__tablename__} (CCTV ID {cctv_id}). Sisa: {current_row_count - rows_to_delete}"
                )

        except Exception as e:
            try:
                db.session.rollback()
            except:
                pass
            thread_name = threading.current_thread().name
            print(f"[{thread_name}] CLEANUP ERROR ({model.__tablename__}): {e}")


def reset_location_data(location_name):
    """
    [FUNGSI ADMIN] Melakukan reset total: menghapus tracker di memori
    dan semua data analitik untuk lokasi ini di database.
    """
    print(
        f"[{threading.current_thread().name}] STARTING COMPLETE ADMIN RESET FOR LOCATION: {location_name}"
    )

    # 💡 PERBAIKAN: Hapus dari GLOBAL_TRACKED_OBJECTS juga!
    if location_name in LOCATION_TRACKERS:
        LOCATION_TRACKERS[location_name].reset_completely()
        LOCATION_TRACKERS.pop(location_name, None)
    if location_name in LATEST_DETECTION_STATS:
        LATEST_DETECTION_STATS.pop(location_name, None)
    if location_name in GLOBAL_TRACKED_OBJECTS:  # 💡 Hapus data kecepatan dari global
        GLOBAL_TRACKED_OBJECTS.pop(location_name, None)

    # 2. Hapus Data Historis dari DATABASE
    try:
        with get_flask_app_context():
            # [BARU] Dapatkan ID untuk penghapusan yang benar
            cctv_id = get_cctv_id_by_location_name(location_name)
            if cctv_id is None:
                print(
                    f"[{threading.current_thread().name}] ERROR: Reset DB dibatalkan karena CCTV ID tidak ditemukan."
                )
                # Lanjut hapus tracker di memori, tapi DB di-skip
                return

            # 💡 Filter Berdasarkan cctv_id yang baru!
            CountingData.query.filter(CountingData.cctv_id == cctv_id).delete(
                synchronize_session=False
            )
            ParkingViolation.query.filter(ParkingViolation.cctv_id == cctv_id).delete(
                synchronize_session=False
            )
            OdolDetection.query.filter(OdolDetection.cctv_id == cctv_id).delete(
                synchronize_session=False
            )
            CrowdDetection.query.filter(CrowdDetection.cctv_id == cctv_id).delete(
                synchronize_session=False
            )
            db.session.commit()
            print(
                f"[{threading.current_thread().name}] Database RESET LENGKAP untuk {location_name} (ID: {cctv_id}) done."
            )

    except Exception as e:
        try:
            db.session.rollback()
        except:
            pass
        print(
            f"[{threading.current_thread().name}] Database cleanup error during reset: {e}"
        )

    print(
        f"[{threading.current_thread().name}] COMPLETE ADMIN RESET FINISHED FOR: {location_name}"
    )


def init_database():
    """
    Fungsi ini sekarang hanya berfungsi untuk menginisialisasi Flask App Context untuk thread worker.
    """
    try:
        with get_flask_app_context():
            pass
        print(
            f"[{threading.current_thread().name}] Database connection initialized for worker."
        )
    except Exception as e:
        print(
            f"[{threading.current_thread().name}] WARNING: DB connection failed on init: {e}"
        )


def save_counting_data(location, counts_jauh, counts_dekat):
    """Menyimpan data counting ke PostgreSQL melalui SQLAlchemy dan membersihkan data lama."""
    try:
        with get_flask_app_context():
            # [BARU] Dapatkan ID CCTV
            cctv_id = get_cctv_id_by_location_name(location)
            if cctv_id is None:
                return

            grand_total = sum(
                counts_jauh.get(c, 0) for c in ["car", "motorcycle", "bus", "truck"]
            ) + sum(
                counts_dekat.get(c, 0) for c in ["car", "motorcycle", "bus", "truck"]
            )

            new_count = CountingData(
                # 💡 GUNAKAN cctv_id!
                cctv_id=cctv_id,
                counts_jauh_car=counts_jauh.get("car", 0),
                counts_jauh_motorcycle=counts_jauh.get("motorcycle", 0),
                counts_jauh_bus=counts_jauh.get("bus", 0),
                counts_jauh_truck=counts_jauh.get("truck", 0),
                counts_dekat_car=counts_dekat.get("car", 0),
                counts_dekat_motorcycle=counts_dekat.get("motorcycle", 0),
                counts_dekat_bus=counts_dekat.get("bus", 0),
                counts_dekat_truck=counts_dekat.get("truck", 0),
                grand_total=grand_total,
            )
            db.session.add(new_count)
            db.session.commit()

            # Panggil cleanup dengan cctv_id
            cleanup_old_data(CountingData, cctv_id, max_rows=200)

    except Exception as e:
        try:
            db.session.rollback()
        except:
            pass
        print(f"[{threading.current_thread().name}] Database error (counting): {e}")


def save_parking_violation(location, vehicle_type, duration, obj_id):
    """Menyimpan data pelanggaran parkir ke PostgreSQL dan membersihkan data lama."""
    try:
        with get_flask_app_context():
            # [BARU] Dapatkan ID CCTV
            cctv_id = get_cctv_id_by_location_name(location)
            if cctv_id is None:
                return

            new_violation = ParkingViolation(
                # 💡 GUNAKAN cctv_id!
                cctv_id=cctv_id,
                vehicle_type=vehicle_type,
                parked_duration_sec=duration,
                object_id=obj_id,
            )
            db.session.add(new_violation)
            db.session.commit()

            # Panggil cleanup dengan cctv_id
            cleanup_old_data(ParkingViolation, cctv_id, max_rows=50)

    except Exception as e:
        try:
            db.session.rollback()
        except:
            pass
        print(f"[{threading.current_thread().name}] Database error (parking): {e}")


def save_crowd_detection(location, crowd_size, duration):
    """Menyimpan data deteksi kerumunan ke PostgreSQL dan membersihkan data lama."""
    try:
        with get_flask_app_context():
            # [BARU] Dapatkan ID CCTV
            cctv_id = get_cctv_id_by_location_name(location)
            if cctv_id is None:
                return

            new_crowd = CrowdDetection(
                # 💡 GUNAKAN cctv_id!
                cctv_id=cctv_id,
                crowd_size=crowd_size,
                duration_sec=duration,
            )
            db.session.add(new_crowd)
            db.session.commit()

            # Panggil cleanup dengan cctv_id
            cleanup_old_data(CrowdDetection, cctv_id, max_rows=50)

    except Exception as e:
        try:
            db.session.rollback()
        except:
            pass
        print(f"[{threading.current_thread().name}] Database error (crowd): {e}")


def save_odol_detection(location, vehicle_type, aspect_ratio, area):
    """Menyimpan data deteksi ODOL ke PostgreSQL dan membersihkan data lama."""
    try:
        with get_flask_app_context():
            # [BARU] Dapatkan ID CCTV
            cctv_id = get_cctv_id_by_location_name(location)
            if cctv_id is None:
                return

            new_odol = OdolDetection(
                # 💡 GUNAKAN cctv_id!
                cctv_id=cctv_id,
                vehicle_type=vehicle_type,
                aspect_ratio=aspect_ratio,
                area=area,
            )
            db.session.add(new_odol)
            db.session.commit()

            # Panggil cleanup dengan cctv_id
            cleanup_old_data(OdolDetection, cctv_id, max_rows=50)

    except Exception as e:
        try:
            db.session.rollback()
        except:
            pass
        print(f"[{threading.current_thread().name}] Database error (odol): {e}")


def delete_all_analytic_data():
    """
    Menghapus SEMUA record dari tabel analitik (counting, parking, crowd, odol).
    Fungsi ini harus dipanggil manual dari Flask Shell atau command.
    """
    with get_flask_app_context():
        try:
            # Menghapus semua baris tanpa filter
            CountingData.query.delete()
            ParkingViolation.query.delete()
            CrowdDetection.query.delete()
            OdolDetection.query.delete()

            db.session.commit()
            print(
                "✅ SUKSES: Semua data analitik (Counting, Parking, Crowd, ODOL) telah dihapus dari PostgreSQL."
            )
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ ERROR saat menghapus semua data: {e}")
            return False


# --- AKHIR FUNGSI DATABASE BARU ---


def detect_objects(frame, confidence_threshold=0.4, classes_to_detect=None):
    if model is None:
        return []
    try:
        results = model(
            frame, conf=confidence_threshold, classes=classes_to_detect, verbose=False
        )
        detections = []
        for result in results:
            boxes = result.boxes
            if boxes is not None:
                for box in boxes:
                    class_id = int(box.cls[0])
                    if classes_to_detect is None or class_id in classes_to_detect:
                        confidence = float(box.conf[0])
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        detection = {
                            "class_id": class_id,
                            "class_name": DETECTION_CLASSES.get(class_id, "unknown"),
                            "confidence": confidence,
                            "bbox": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
                            "center": [int((x1 + x2) / 2), int((y1 + y2) / 2)],
                            "aspect_ratio": (
                                (y2 - y1) / (x2 - x1) if (x2 - x1) > 0 else 0
                            ),
                            "area": (x2 - x1) * (y2 - y1),
                        }
                        detections.append(detection)
        return detections
    except Exception as e:
        print(f"[{threading.current_thread().name}] Detection error: {e}")
        return []


def draw_bounding_boxes(
    frame,
    tracked_objects,
    tracker,
    location_name="unknown",
    crowd_member_ids=None,
    is_crowd=False,
    config=None,  # 💡 Terima config
):
    if crowd_member_ids is None:
        crowd_member_ids = set()
    output_frame = frame.copy()

    # 💡 Ambil konfigurasi (fallback ke hardcode jika config tidak diteruskan)
    config = config if config is not None else {}
    line1_y = config.get("line1_y", DEFAULT_LINE_1_Y)
    line2_y = config.get("line2_y", DEFAULT_LINE_2_Y)

    # 💡 Perbaikan Garis: Gunakan variabel dinamis
    cv2.line(output_frame, (0, line1_y), (600, line1_y), (0, 255, 255), 2)
    cv2.line(output_frame, (0, line2_y), (600, line2_y), (0, 255, 255), 2)

    # ... [Kode Draw Panah: Gunakan variabel dinamis line1_y/line2_y] ...
    ARROW_SIZE = 15
    ARROW_COLOR_JAUH = (0, 255, 0)
    ARROW_COLOR_DEKAT = (0, 0, 255)

    triangle_jauh_coords = np.array(
        [
            [200 - ARROW_SIZE, line1_y],
            [200 + ARROW_SIZE, line1_y],
            [200, line1_y - ARROW_SIZE],
        ],
        np.int32,
    )
    cv2.fillPoly(output_frame, [triangle_jauh_coords], ARROW_COLOR_JAUH)

    triangle_dekat_coords = np.array(
        [
            [450 - ARROW_SIZE, line2_y],
            [450 + ARROW_SIZE, line2_y],
            [450, line2_y + ARROW_SIZE],
        ],
        np.int32,
    )
    cv2.fillPoly(output_frame, [triangle_dekat_coords], ARROW_COLOR_DEKAT)

    # ... [Kode Draw Tabel] ...
    COL_LABELS = ["Arah", "MOBIL", "MOTOR", "BUS", "TRUK"]
    START_X = 10
    START_Y = 12
    COL_WIDTH = 50
    ROW_HEIGHT = 16
    FONT_SCALE = 0.35
    FONT_THICKNESS = 1
    CENTER_OFFSET = 15
    TEXT_HEIGHT_PAD = 3

    TABLE_X_END = START_X + len(COL_LABELS) * COL_WIDTH
    TABLE_Y_END = 5 + 3 * ROW_HEIGHT + 3
    BORDER_COLOR = (100, 100, 100)

    cv2.rectangle(output_frame, (5, 5), (TABLE_X_END, TABLE_Y_END), (0, 0, 0), -1)
    cv2.line(
        output_frame,
        (5, START_Y + 4),
        (TABLE_X_END, START_Y + 4),
        BORDER_COLOR,
        FONT_THICKNESS,
    )
    cv2.line(
        output_frame,
        (5, START_Y + ROW_HEIGHT + 4),
        (TABLE_X_END, START_Y + ROW_HEIGHT + 4),
        BORDER_COLOR,
        FONT_THICKNESS,
    )
    cv2.line(
        output_frame,
        (5, TABLE_Y_END),
        (TABLE_X_END, TABLE_Y_END),
        BORDER_COLOR,
        FONT_THICKNESS,
    )

    for i in range(len(COL_LABELS) + 1):
        x_pos = START_X + i * COL_WIDTH
        cv2.line(
            output_frame, (x_pos, 5), (x_pos, TABLE_Y_END), BORDER_COLOR, FONT_THICKNESS
        )

    Y_HEADER_TEXT = START_Y
    for i, label in enumerate(COL_LABELS):
        x_pos = START_X + i * COL_WIDTH
        cv2.putText(
            output_frame,
            label,
            (x_pos + 3, Y_HEADER_TEXT),
            cv2.FONT_HERSHEY_SIMPLEX,
            FONT_SCALE,
            (255, 255, 255),
            FONT_THICKNESS,
        )

    Y_ROW_ATAS = START_Y + ROW_HEIGHT
    counts_jauh = tracker.counts_menuju_jauh
    cv2.putText(
        output_frame,
        "ATAS",
        (START_X + 3, Y_ROW_ATAS + TEXT_HEIGHT_PAD),
        cv2.FONT_HERSHEY_SIMPLEX,
        FONT_SCALE,
        (0, 255, 0),
        FONT_THICKNESS,
    )
    categories = ["car", "motorcycle", "bus", "truck"]
    for i, category in enumerate(categories):
        x_pos = START_X + (i + 1) * COL_WIDTH + CENTER_OFFSET
        count_val = counts_jauh.get(category, 0)
        cv2.putText(
            output_frame,
            str(count_val),
            (x_pos, Y_ROW_ATAS + TEXT_HEIGHT_PAD),
            cv2.FONT_HERSHEY_SIMPLEX,
            FONT_SCALE,
            (0, 255, 0),
            FONT_THICKNESS,
        )

    Y_ROW_BAWAH = START_Y + 2 * ROW_HEIGHT
    counts_dekat = tracker.counts_menuju_dekat
    cv2.putText(
        output_frame,
        "BAWAH",
        (START_X + 3, Y_ROW_BAWAH + TEXT_HEIGHT_PAD),
        cv2.FONT_HERSHEY_SIMPLEX,
        FONT_SCALE,
        (0, 255, 255),
        FONT_THICKNESS,
    )
    for i, category in enumerate(categories):
        x_pos = START_X + (i + 1) * COL_WIDTH + CENTER_OFFSET
        count_val = counts_dekat.get(category, 0)
        cv2.putText(
            output_frame,
            str(count_val),
            (x_pos, Y_ROW_BAWAH + TEXT_HEIGHT_PAD),
            cv2.FONT_HERSHEY_SIMPLEX,
            FONT_SCALE,
            (0, 255, 255),
            FONT_THICKNESS,
        )

    if is_crowd:
        pass

    # 💡 Ambil data kecepatan dari GLOBAL_TRACKED_OBJECTS[location_name]
    local_speed_data = GLOBAL_TRACKED_OBJECTS.get(location_name, {})

    for object_id, obj in tracked_objects.items():
        bbox = obj["bbox"]
        class_name = obj["class_name"]
        x, y, w, h = bbox

        color = COLORS.get(class_name, (255, 255, 255))

        # --- Penambahan dari Remote: Label Speed ---
        # 💡 Gunakan local_speed_data
        speed = local_speed_data.get(object_id, {}).get("speed", 0)

        # 💡 PERBAIKAN MERGE CONFLICT: Ambil Plat Nomor dari tracker.objects
        plat_nomor = tracker.objects[object_id].get("plate_number")

        # --- FORMAT LABEL ---
        if plat_nomor:
            label_text = f"{object_id} [{speed:.1f} km/h] {plat_nomor}"
            color = (0, 255, 0)  # HIJAU = Sudah discan
        else:
            label_text = f"{object_id} [{speed:.1f} km/h]"
        # --- Akhir Penambahan dari Remote ---

        if class_name == "person" and object_id in crowd_member_ids:
            color = COLORS["crowd"]

        if tracker:
            if tracker.is_parked.get(object_id, False):
                color = COLORS["parking"]
                label_text = "PARKIR LIAR"
            elif tracker.is_odol_logged.get(object_id, False):
                color = COLORS["odol"]
                label_text = "ODOL"

        font_scale = 0.28
        padding = 2
        font_thickness = 1
        (text_width, text_height), baseline = cv2.getTextSize(
            label_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness
        )
        label_bg_y1 = y - text_height - padding * 2
        label_bg_y2 = y
        if label_bg_y1 < 0:
            label_bg_y1 = y
            label_bg_y2 = y + text_height + padding * 2

        cv2.rectangle(
            output_frame,
            (x, label_bg_y1),
            (x + text_width + padding, label_bg_y2),
            color,
            -1,
        )
        cv2.putText(
            output_frame,
            label_text,
            (x + int(padding / 2), y - padding),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            font_thickness,
        )
        cv2.rectangle(output_frame, (x, y), (x + w, y + h), color, 1)

    return output_frame


def get_stream_from_m3u8(m3u8_url, max_retries=3):
    for attempt in range(max_retries):
        try:
            cap = cv2.VideoCapture(m3u8_url)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ret, test_frame = cap.read()
            if ret and test_frame is not None:
                print(
                    f"[{threading.current_thread().name}] Stream connected successfully: {m3u8_url}"
                )
                return cap
            else:
                cap.release()
        except Exception as e:
            print(
                f"[{threading.current_thread().name}] Stream connection attempt {attempt + 1} failed: {e}"
            )
            try:
                if cap:
                    cap.release()
            except:
                pass
            if attempt < max_retries - 1:
                time.sleep(2)
    print(
        f"[{threading.current_thread().name}] Failed to connect to stream after {max_retries} attempts"
    )
    return None


def recognize_plate(frame, vehicle_bbox):
    """
    Mendeteksi plat nomor menggunakan best.pt lalu membacanya dengan OCR.
    """
    if READER_OCR is None or plate_model is None:
        return "System Error"

    # 1. Crop Gambar Kendaraan
    x, y, w, h = vehicle_bbox
    h_img, w_img, _ = frame.shape

    # Validasi koordinat agar tidak error
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(w_img, x + w), min(h_img, y + h)

    vehicle_crop = frame[y1:y2, x1:x2]

    if vehicle_crop.size == 0:
        return "Gagal Crop"

    try:
        # 2. Deteksi Lokasi Plat menggunakan best.pt
        # conf=0.25 artinya minimal yakin 25% itu plat
        results = plate_model(vehicle_crop, conf=0.25, verbose=False)

        plate_text = "Tidak Terbaca"

        for result in results:
            boxes = result.boxes
            if len(boxes) > 0:
                # Ambil deteksi dengan confidence tertinggi
                best_box = boxes[0]
                px1, py1, px2, py2 = best_box.xyxy[0].tolist()

                # 3. Crop Gambar Plat
                # Koordinat plat ini relatif terhadap vehicle_crop
                plate_img = vehicle_crop[int(py1) : int(py2), int(px1) : int(px2)]

                # Preprocessing sedikit biar OCR makin jago (Grayscale)
                gray_plate = cv2.cvtColor(plate_img, cv2.COLOR_BGR2GRAY)
                _, binary_plate = cv2.threshold(
                    gray_plate, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
                )

                # 4. Baca Teks pakai EasyOCR
                ocr_result = READER_OCR.readtext(
                    binary_plate,
                    detail=0,
                    allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                )

                if ocr_result:
                    # Gabungkan semua teks yang terbaca
                    raw_text = "".join(ocr_result).upper().replace(" ", "")

                    # --- FILTER REGEX (PLAT INDONESIA) ---
                    # Pola: 1-2 Huruf awal + 1-4 Angka + 1-3 Huruf akhir
                    pola_plat = r"^[A-Z]{1,2}\d{1,4}[A-Z]{1,3}$"

                    match = re.match(pola_plat, raw_text)

                    if match:
                        clean_text = raw_text
                        plate_text = clean_text
                        print(f"🎯 PLAT VALID DITEMUKAN: {plate_text}")
                        return plate_text  # Langsung kembalikan yang valid
                    else:
                        print(f"⚠️ Teks dibuang (Bukan Plat): {raw_text}")

        return "Tidak Terbaca"

    except Exception as e:
        print(f"Error proses plat: {e}")
        return "Error"


def detect_crowd(tracked_objects, min_crowd_size=2, crowd_radius_threshold=100):
    """
    Mendeteksi kerumunan dari objek 'person' yang terlacak.
    """
    people_objects = {
        oid: obj
        for oid, obj in tracked_objects.items()
        if obj["class_name"] == "person"
    }

    if len(people_objects) < min_crowd_size:
        return False, set()

    centers = []
    obj_ids = []
    for obj_id, obj in people_objects.items():
        centers.append(obj["center"])
        obj_ids.append(obj_id)

    n = len(centers)
    adj_matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            dist = math.sqrt(
                (centers[i][0] - centers[j][0]) ** 2
                + (centers[i][1] - centers[j][1]) ** 2
            )
            if dist < crowd_radius_threshold:
                adj_matrix[i][j] = 1
                adj_matrix[j][i] = 1

    visited = [False] * n
    crowd_member_ids = set()
    for i in range(n):
        if not visited[i]:
            component = []
            queue = [i]
            visited[i] = True
            idx = 0
            while idx < len(queue):
                u = queue[idx]
                idx += 1
                component.append(u)
                for v in range(n):
                    if adj_matrix[u][v] == 1 and not visited[v]:
                        visited[v] = True
                        queue.append(v)
            if len(component) >= min_crowd_size:
                for idx_in_component in component:
                    crowd_member_ids.add(obj_ids[idx_in_component])

    return len(crowd_member_ids) > 0, crowd_member_ids


def get_stream(stream_url):
    # Jika RTSP
    if stream_url.startswith("rtsp://"):
        print(f"[{threading.current_thread().name}] Mencoba membuka RTSP stream...")
        cap = cv2.VideoCapture(stream_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        ret, test_frame = cap.read()
        if ret and test_frame is not None:
            print(f"[{threading.current_thread().name}] RTSP connected successfully.")
            return cap
        else:
            print(f"[{threading.current_thread().name}] RTSP gagal dibuka.")
            cap.release()
            return None

    # Jika M3U8
    elif stream_url.endswith(".m3u8"):
        print(f"[{threading.current_thread().name}] Membuka stream M3U8...")
        return get_stream_from_m3u8(stream_url)

    else:
        print(
            f"[{threading.current_thread().name}] Format stream tidak dikenal: {stream_url}"
        )
        return None


# -------------------------------------------------------------------
# FUNGSI WORKER (OTAK DETEKSI 24/7) - VERSI TANGGUH
# -------------------------------------------------------------------
def run_detection_worker(
    stream_url, location_name, detection_mode="all", stop_event=None
):

    thread_name = threading.current_thread().name
    print(f"[{thread_name}] WORKER DIMULAI: Memulai deteksi untuk: {location_name}")
    init_database()

    # ---- TAMBAHKAN 3 BARI INI ----
    last_odol_notification_time = {}
    last_parking_notification_time = {}
    last_crowd_notification_time = {}
    # ------------------------------

    # ----------------------------------------------------------------------
    # 💡 LANGKAH 1: MEMUAT KONFIGURASI DINAMIS DARI DB/DEFAULT
    # ----------------------------------------------------------------------
    cctv_config_data = {
        "line1_y": DEFAULT_LINE_1_Y,
        "line2_y": DEFAULT_LINE_2_Y,
        "real_distance_m": DEFAULT_REAL_DISTANCE,
        "pixel_distance": abs(DEFAULT_LINE_2_Y - DEFAULT_LINE_1_Y),
    }

    try:
        with get_flask_app_context():
            # Cari objek CCTV (Asumsi Anda sudah punya model CCTV yang diimpor)
            cctv_obj = CCTV.query.filter_by(lokasi=location_name).first()
            if cctv_obj:
                # Ambil nilai dari DB, fallback ke DEFAULT jika None
                l1 = getattr(cctv_obj, "line1_y", DEFAULT_LINE_1_Y)
                l2 = getattr(cctv_obj, "line2_y", DEFAULT_LINE_2_Y)
                dist = getattr(cctv_obj, "real_distance_m", DEFAULT_REAL_DISTANCE)

                cctv_config_data["line1_y"] = l1
                cctv_config_data["line2_y"] = l2
                cctv_config_data["real_distance_m"] = dist
                # Pastikan pixel_distance tidak nol (untuk menghindari ZeroDivisionError)
                cctv_config_data["pixel_distance"] = max(1, abs(l2 - l1))

    except Exception as e:
        print(
            f"[{thread_name}] WARNING: Gagal memuat config CCTV dari DB: {e}. Menggunakan default."
        )

    # 💥 TAMBAHAN KRITIS: PAKSA CLEANUP SAAT WORKER BARU DIMULAI
    try:
        with get_flask_app_context():
            # [BARU] Dapatkan ID CCTV untuk cleanup awal
            cctv_id = get_cctv_id_by_location_name(location_name)
            if cctv_id is not None:
                cleanup_old_data(CountingData, cctv_id, max_rows=200)
                cleanup_old_data(ParkingViolation, cctv_id, max_rows=50)
                cleanup_old_data(CrowdDetection, cctv_id, max_rows=50)
                cleanup_old_data(OdolDetection, cctv_id, max_rows=50)
                db.session.commit()
                print(
                    f"[{thread_name}] DEBUG: Initial cleanup completed using cctv_id {cctv_id}."
                )
            else:
                print(
                    f"[{thread_name}] DEBUG: Initial cleanup skipped, CCTV ID not found for {location_name}."
                )

    except Exception as e:
        print(f"[{thread_name}] DEBUG: Initial cleanup failed: {e}")

    # ----------------------------------------------------------------------

    # ----------------------------------------------------------------------
    # 💡 LANGKAH 2: MANAJEMEN TRACKER DAN GLOBAL SPEED DATA (HINDARI RACE CONDITION)
    # ----------------------------------------------------------------------

    # 💥 Mengelola Tracker (Counting/Object ID) - Continuation Logic
    if location_name not in LOCATION_TRACKERS:
        tracker = SimpleObjectTracker(max_disappeared=80, max_distance=280, fps=10)
        LOCATION_TRACKERS[location_name] = tracker
        print(f"[{thread_name}] WORKER INFO: Tracker baru dibuat. Mulai dari nol.")
    else:
        tracker = LOCATION_TRACKERS[location_name]
        print(
            f"[{thread_name}] WORKER INFO: Menggunakan tracker yang sudah ada. Melanjutkan hitungan."
        )

    # 💥 Mengelola Global Speed Data (TRACKED) - Race Condition Fix
    if location_name not in GLOBAL_TRACKED_OBJECTS:
        GLOBAL_TRACKED_OBJECTS[location_name] = {}
    local_speed_data = GLOBAL_TRACKED_OBJECTS[
        location_name
    ]  # Referensi lokal ke data lokasi ini

    if location_name not in LATEST_DETECTION_STATS:
        LATEST_DETECTION_STATS[location_name] = {
            "latest_frame": np.zeros((480, 640, 3), dtype=np.uint8),
            "current_tracked_objects": {},
            "crowd_member_ids": set(),
            "is_crowd_detected": False,
            "total_counts": {},
            "stat_parkir": 0,
            "stat_odol": 0,
            "stat_peringatan_aktif": 0,
            "stat_total_pelanggaran": 0,
        }

    frame_count = 0
    crowd_currently_logged = False
    is_crowd = False
    session_parking_count = 0
    session_odol_count = 0
    cap = None

    # ----------------------------------------------------------------------

    try:
        while True:
            if stop_event and stop_event.is_set():
                print(
                    f"[{thread_name}] WORKER INFO: Menerima sinyal berhenti. Keluar dari loop."
                )
                break
            # ... [Kode koneksi stream] ...
            if cap is None:
                print(
                    f"[{thread_name}] WORKER INFO: Mencoba koneksi ke stream {location_name}..."
                )
                cap = get_stream(stream_url)

                if cap is None:
                    continue
                else:
                    print(
                        f"[{thread_name}] WORKER INFO: Koneksi {location_name} berhasil."
                    )
                    try:
                        socketio.emit(
                            "stream_ready",
                            {"location": location_name, "status": "ready"},
                        )
                    except Exception as e:
                        print(f"[{thread_name}] SocketIO emit error (ready): {e}")

            start_time = time.time()
            ret, frame = cap.read()

            if not ret or frame is None:
                print(
                    f"[{thread_name}] WORKER ERROR: Stream {location_name} terputus. Menutup koneksi lama..."
                )
                cap.release()
                cap = None
                time.sleep(5)
                continue

            if frame.shape[1] > 600:
                h_orig, w_orig = frame.shape[:2]
                frame = cv2.resize(frame, (600, int(h_orig * 600 / w_orig)))
                if not hasattr(cv2, "has_saved_frame"):
                    cv2.imwrite("last_resized_frame.jpg", frame)
                    cv2.has_saved_frame = True

            detections = []
            tracked_objects = {}

            if frame_count % 3 == 0:
                detections = detect_objects(
                    frame, confidence_threshold=0.5, classes_to_detect=[0, 2, 3, 5]
                )

            # 💡 PERUBAHAN: Panggil update dengan konfigurasi & data kecepatan lokal
            tracked_objects = tracker.update(
                detections, cctv_config_data, local_speed_data
            )

            # ==========================================================
            # LOGIKA: AUTO-SCAN SEMUA KENDARAAN (AGAR PLAT MUNCUL)
            # ==========================================================

            # Cek setiap 5 frame sekali supaya laptop tidak berat
            if frame_count % 5 == 0:

                for obj_id, obj in tracked_objects.items():
                    # Hanya proses Mobil, Motor, Bus, Truk
                    if obj["class_name"] in ["car", "motorcycle", "bus", "truck"]:

                        # CEK PENTING: Apakah mobil ini SUDAH punya plat?
                        # Kalau SUDAH ada di memori, SKIP (JANGAN SCAN LAGI) -> Hemat CPU
                        if "plate_number" in tracker.objects[obj_id]:
                            continue

                        # Kalau BELUM ada, Scan Platnya sekarang!
                        # Filter ukuran: hanya scan kalau objek cukup besar (biar akurat)
                        area_objek = obj["bbox"][2] * obj["bbox"][3]
                        if area_objek > 3000:

                            # Panggil Model best.pt + EasyOCR
                            plat = recognize_plate(frame, obj["bbox"])

                            # Jika berhasil terbaca
                            if plat and plat != "Tidak Terbaca" and len(plat) > 2:
                                # SIMPAN KE MEMORI (Agar muncul terus di layar)
                                tracker.objects[obj_id]["plate_number"] = plat
                                print(f"✅ PLAT TERDETEKSI: ID {obj_id} -> {plat}")

            # ==========================================================

            is_crowd, crowd_ids = detect_crowd(
                tracked_objects, min_crowd_size=5, crowd_radius_threshold=40
            )
            # ... [Kode pelanggaran (crowd, parking, odol)] ...

            # KODE PENGGANTI LENGKAP UNTUK KERUMUNAN
            if is_crowd and not crowd_currently_logged:
                # ---- TAMBAHKAN LOGIKA WAKTU ----
                current_time = time.time()
                last_notification_time = last_crowd_notification_time.get(
                    location_name, 0
                )

                # Hanya kirim notifikasi jika sudah 30 detik sejak notifikasi kerumunan terakhir di lokasi ini
                if (current_time - last_notification_time) > 30:
                    print(
                        f"[{thread_name}] DEBUG CROWD TRIGGER (WITH TIME CHECK): Crowd Size {len(crowd_ids)}, Location: {location_name}"
                    )

                    first_seen_time = (
                        tracker.first_seen.get(list(crowd_ids)[0], current_time)
                        if crowd_ids
                        else current_time
                    )
                    duration = current_time - first_seen_time
                    save_crowd_detection(location_name, len(crowd_ids), duration)
                    crowd_currently_logged = True

                    # Simpan waktu notifikasi terakhir
                    last_crowd_notification_time[location_name] = current_time

                    if GLOBAL_NOTIFICATION_ENABLED:
                        try:
                            socketio.emit(
                                "notifikasi_baru",
                                {
                                    "title": "🚨 Kerumunan Terdeteksi!",
                                    "detail": f"Terdeteksi {len(crowd_ids)} orang berkumpul di {location_name}.",
                                    "icon": "warning",
                                    "location": location_name,
                                },
                            )
                        except Exception as e:
                            print(f"[{thread_name}] SocketIO emit error (crowd): {e}")
            elif not is_crowd:
                crowd_currently_logged = False

            current_time = time.time()
            for obj_id, obj in tracked_objects.items():
                if obj["class_name"] in ["car", "motorcycle", "bus"]:

                    last_move = tracker.last_moved.get(obj_id, current_time)
                    parked_duration = current_time - last_move
                    was_parked = tracker.is_parked.get(obj_id, False)
                    now_parked = parked_duration > 50

                    # KODE PENGGANTI LENGKAP UNTUK PARKIR LIAR
                    if not was_parked and now_parked:
                        # --- INTEGRASI BARU: Mendeteksi Plat Saat Parkir Liar Terdeteksi ---
                        detected_plate = (
                            recognize_plate(frame, obj["bbox"])
                            if "plate_number" not in tracker.objects[obj_id]
                            else tracker.objects[obj_id]["plate_number"]
                        )
                        tracker.objects[obj_id]["plate_number"] = detected_plate
                        # -----------------------------------------------------------------

                        # ---- TAMBAHKAN LOGIKA WAKTU ----
                        current_time = time.time()
                        last_notification_time = last_parking_notification_time.get(
                            location_name, 0
                        )

                        # Hanya kirim notifikasi jika sudah 20 detik sejak notifikasi parkir terakhir di lokasi ini
                        if (current_time - last_notification_time) > 20:
                            print(
                                f"[{thread_name}] DEBUG PARKIR TRIGGER (WITH TIME CHECK): Obj ID {obj_id}, Location: {location_name}"
                            )

                            save_parking_violation(
                                location_name,
                                obj["class_name"],
                                parked_duration,
                                obj_id,
                            )
                            session_parking_count += 1

                            # Simpan waktu notifikasi terakhir
                            last_parking_notification_time[location_name] = current_time

                            if GLOBAL_NOTIFICATION_ENABLED:
                                try:
                                    socketio.emit(
                                        "notifikasi_baru",
                                        {
                                            "title": "🅿️ Parkir Liar Terdeteksi!",
                                            "detail": f'{obj["class_name"]} (Plat: {detected_plate}) parkir liar di {location_name}.',
                                            "icon": "error",
                                            "location": location_name,
                                        },
                                    )
                                except Exception as e:
                                    print(f"SocketIO error: {e}")
                        # ------------------------------------
                        tracker.is_parked[obj_id] = now_parked

                    if obj["class_name"] == "bus":
                        aspect_ratio = obj.get("aspect_ratio", 0)
                        area = obj["bbox"][2] * obj["bbox"][3]
                        is_odol = aspect_ratio > 0.8 or area > 10000
                        was_odol_logged = tracker.is_odol_logged.get(obj_id, False)

                        # KODE PENGGANTI LENGKAP UNTUK ODOL
                        if is_odol and not was_odol_logged:
                            # ---- TAMBAHKAN LOGIKA WAKTU ----
                            current_time = time.time()
                            last_notification_time = last_odol_notification_time.get(
                                location_name, 0
                            )

                            # Hanya kirim notifikasi jika sudah 15 detik sejak notifikasi ODOL terakhir di lokasi ini
                            # KODE YANG SUDAH DIPERBAIKI UNTUK ODOL
                            if (current_time - last_notification_time) > 15:
                                print(
                                    f"[{thread_name}] DEBUG ODOL TRIGGER (WITH TIME CHECK): Obj ID {obj_id}, Location: {location_name}"
                                )

                                save_odol_detection(
                                    location_name, obj["class_name"], aspect_ratio, area
                                )
                                tracker.is_odol_logged[obj_id] = True
                                session_odol_count += 1

                                last_odol_notification_time[location_name] = (
                                    current_time
                                )

                                # ---- TAMBAHKAN KONDISI GLOBAL INI ----
                                if GLOBAL_NOTIFICATION_ENABLED:
                                    try:
                                        socketio.emit(
                                            "notifikasi_baru",
                                            {
                                                "title": "🚚 Deteksi ODOL!",
                                                "detail": f'Kendaraan {obj["class_name"]} (ID: {obj_id}) terindikasi ODOL di {location_name}.',
                                                "icon": "info",
                                                "location": location_name,
                                            },
                                        )
                                    except Exception as e:
                                        print(
                                            f"[{thread_name}] SocketIO emit error (odol): {e}"
                                        )

            frame_count += 1

            # --- LOGIKA PENYIMPANAN COUNTING BERKALA ---
            if frame_count % 60 == 0:
                counts_jauh_data = dict(tracker.counts_menuju_jauh)
                counts_dekat_data = dict(tracker.counts_menuju_dekat)
                save_counting_data(location_name, counts_jauh_data, counts_dekat_data)
            # --- AKHIR LOGIKA PENYIMPANAN COUNTING BERKALA ---

            # 💡 PERUBAHAN: Panggil draw dengan konfigurasi dinamis
            output_frame = draw_bounding_boxes(
                frame,
                tracked_objects,
                tracker,
                location_name=location_name,
                crowd_member_ids=crowd_ids,
                is_crowd=is_crowd,
                config=cctv_config_data,
            )

            counts_jauh = dict(tracker.counts_menuju_jauh)
            counts_dekat = dict(tracker.counts_menuju_dekat)

            active_parked_count = sum(
                1 for parked in tracker.is_parked.values() if parked
            )
            active_odol_count = sum(
                1 for odol in tracker.is_odol_logged.values() if odol
            )
            active_crowd_count = 1 if is_crowd else 0
            session_peringatan_aktif = (
                active_parked_count + active_odol_count + active_crowd_count
            )

            ret_enc, jpeg_buffer = cv2.imencode(".jpg", output_frame)
            frame_bytes_untuk_dikirim = jpeg_buffer.tobytes() if ret_enc else None

            current_stats = {
                "latest_frame": frame_bytes_untuk_dikirim,
                "current_tracked_objects": tracked_objects,
                "crowd_member_ids": list(crowd_ids),
                "is_crowd_detected": is_crowd,
                "counts_jauh": counts_jauh,
                "counts_dekat": counts_dekat,
                "stat_parkir": session_parking_count,
                "stat_odol": session_odol_count,
                "stat_peringatan_aktif": session_peringatan_aktif,
                "stat_total_pelanggaran": session_parking_count + session_odol_count,
            }

            LATEST_DETECTION_STATS[location_name] = current_stats

            if frame_count % 10 == 0:
                speed_values = []
                # Hitung kecepatan rata-rata dari data lokal
                for obj_id, obj in tracked_objects.items():
                    if obj["class_name"] in ["car", "motorcycle", "bus", "truck"]:
                        if (
                            obj_id in local_speed_data
                            and "speed" in local_speed_data[obj_id]
                        ):
                            speed_values.append(local_speed_data[obj_id]["speed"])
                        else:
                            speed_values.append(0)

                valid_speed_values = [s for s in speed_values if s > 0]
                avg_speed = (
                    sum(valid_speed_values) / len(valid_speed_values)
                    if valid_speed_values
                    else 0
                )

                speed_jauh = []
                speed_dekat = []

                # Filter speed berdasarkan zona (menggunakan config dinamis)
                line1_y_local = cctv_config_data["line1_y"]
                line2_y_local = cctv_config_data["line2_y"]

                for obj_id, obj in local_speed_data.items():
                    try:
                        last_y = obj["last_y"]
                        if last_y < line1_y_local and obj["speed"] > 0:
                            speed_jauh.append(obj["speed"])
                        elif last_y > line2_y_local and obj["speed"] > 0:
                            speed_dekat.append(obj["speed"])
                    except:
                        pass

                avg_speed_jauh = sum(speed_jauh) / len(speed_jauh) if speed_jauh else 0
                avg_speed_dekat = (
                    sum(speed_dekat) / len(speed_dekat) if speed_dekat else 0
                )

                stats_packet = {
                    "location": location_name,
                    "is_crowd_detected": is_crowd,
                    "counts_jauh_mobil": counts_jauh.get("car", 0),
                    "counts_jauh_motor": counts_jauh.get("motorcycle", 0),
                    "counts_jauh_bus": counts_jauh.get("bus", 0),
                    "counts_jauh_truck": counts_jauh.get("truck", 0),
                    "counts_jauh_orang": counts_jauh.get("person", 0),
                    "counts_dekat_mobil": counts_dekat.get("car", 0),
                    "counts_dekat_motor": counts_dekat.get("motorcycle", 0),
                    "counts_dekat_bus": counts_dekat.get("bus", 0),
                    "counts_dekat_truck": counts_dekat.get("truck", 0),
                    "counts_dekat_orang": counts_dekat.get("person", 0),
                    "avg_speed": avg_speed,
                    "speed_jauh": avg_speed_jauh,
                    "speed_dekat": avg_speed_dekat,
                    "stat_parkir": session_parking_count,
                    "stat_odol": session_odol_count,
                }

                socketio.emit("update_stats_realtime", stats_packet)

            end_time = time.time()
            elapsed_time = end_time - start_time
            target_fps = 10
            target_delay = 1.0 / target_fps
            if elapsed_time < target_delay:
                time.sleep(target_delay - elapsed_time)

    except Exception as e:
        print(f"[{thread_name}] Error Kritis di WORKER {location_name}: {e}")
    finally:
        if cap:
            cap.release()
        if location_name in LATEST_DETECTION_STATS:
            del LATEST_DETECTION_STATS[location_name]

        print(f"[{thread_name}] WORKER {location_name} telah berhenti.")


# -------------------------------------------------------------------
# FUNGSI STREAMER (Tidak Diubah)
# -------------------------------------------------------------------
def generate_frames(stream_url, location_name, detection_mode="simple"):
    """
    Fungsi ini HANYA mengambil frame video yang sudah digambar oleh worker
    dari LATEST_DETECTION_STATS dan menayangkannya ke browser.
    """
    location_name = str(location_name).strip()
    print(
        f"[{threading.current_thread().name}] STREAM DIMULAI: Menyalankan video untuk: {location_name}"
    )

    error_frame_bytes = None
    try:
        error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(
            error_frame,
            "Stream tidak tersedia",
            (50, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 0, 255),
            2,
        )
        ret, buffer = cv2.imencode(".jpg", error_frame)
        if ret:
            error_frame_bytes = buffer.tobytes()
    except Exception as e:
        print(f"Error membuat frame error: {e}")

    try:
        while True:
            frame_to_yield = None

            if location_name in LATEST_DETECTION_STATS:
                stats = LATEST_DETECTION_STATS[location_name]
                frame_data = stats.get("latest_frame")

                if isinstance(frame_data, np.ndarray):
                    ret, buffer = cv2.imencode(".jpg", frame_data)
                    if ret:
                        frame_to_yield = buffer.tobytes()

                elif isinstance(frame_data, bytes):
                    frame_to_yield = frame_data

                if frame_to_yield:
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                        + frame_to_yield
                        + b"\r\n"
                    )
                else:
                    if error_frame_bytes:
                        yield (
                            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                            + error_frame_bytes
                            + b"\r\n"
                        )

            else:
                if error_frame_bytes:
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                        + error_frame_bytes
                        + b"\r\n"
                    )

            time.sleep(1.0 / 20)

    except GeneratorExit:
        print(
            f"[{threading.current_thread().name}] STREAM DIHENTIKAN: Klien menutup koneksi untuk {location_name}."
        )
    except Exception as e:
        print(
            f"[{threading.current_thread().name}] Error di generate_frames (streaming) untuk {location_name}: {e}"
        )
    finally:
        print(
            f"[{threading.current_thread().name}] Sesi STREAMING untuk {location_name} selesai."
        )


def get_counting_summary(location, hours=24):
    pass


def generate_frames_preview_only(stream_url):
    pass


if __name__ == "__main__":
    print("Analyzer module ready. Must be run via run.py.")
