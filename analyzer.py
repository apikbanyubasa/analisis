import cv2
import easyocr
import sqlite3
import numpy as np
from datetime import datetime
import time
import threading
from ultralytics import YOLO
import os
from collections import defaultdict, deque
import math



# --- Impor SocketIO dari Server ---
try:
    from app import socketio
    print("Berhasil mengimpor SocketIO Server (app.socketio) untuk notifikasi real-time.")
except ImportError:
    print("PERINGATAN: Gagal mengimpor SocketIO. Notifikasi real-time tidak akan berfungsi.")
    class DummySocketIO:
        def emit(self, *args, **kwargs):
            pass 
    socketio = DummySocketIO()
# --- AKHIR IMPOR ---


# Load YOLO model
try:
    model = YOLO("models/yolov8n.pt")
    print("YOLO model loaded successfully")
except Exception as e:
    print(f"Error loading YOLO model: {e}")
    model = None


# Load EasyOCR Reader
try:
    # --- DINONAKTIFKAN SEMENTARA UNTUK TES LAG ---
    READER_OCR = None 
    print("EasyOCR reader dinonaktifkan untuk tes performa.")
    # --- AKHIR PERUBAHAN ---
except Exception as e:
    print(f"Error loading EasyOCR: {e}")
    READER_OCR = None


# Konfigurasi deteksi
DETECTION_CLASSES = {
    0: "person", 2: "car", 3: "motorcycle", 5: "bus",
    7: "truck", # <-- INI WAJIB ADA UNTUK MENGHITUNG
}
COLORS = {
    "person": (30, 144, 255), 
    "car": (30, 255, 144), 
    "motorcycle": (255, 255, 30), 
    "bus": (144, 30, 255),
    "truck": (255, 165, 0), # <-- WARNA BARU UNTUK TRUK (Orange)
    "crowd": (0, 0, 255), 
    "parking": (255, 0, 0), 
    "odol": (0, 255, 255)
}

# --- 🚀 AWAL PERUBAHAN 1 ---

# 1. MASUKKAN KOORDINAT (x,y) ANDA DI SINI
# Garis yang lebih JAUH dari kamera (angka y lebih kecil)
LINE_1_COORDS = ((0, 198), (600, 198))
# Garis yang lebih DEKAT dari kamera (angka y lebih besar)
LINE_2_COORDS = ((0, 244), (600, 244))

# 2. DEFINISIKAN ZONA
ZONE_JAUH = 1   # Area di atas LINE_1
ZONE_TENGAH = 2 # Area di antara LINE_1 dan LINE_2
ZONE_DEKAT = 3  # Area di bawah LINE_2

# 3. TAMBAHKAN FUNGSI HELPER INI
def get_point_side(point_x, point_y, line_x1, line_y1, line_x2, line_y2):
    """
    Menghitung sisi sebuah titik (x, y) relatif terhadap sebuah garis.
    Mengembalikan > 0 (satu sisi), < 0 (sisi lain), atau 0 (tepat di garis).
    """
    return (point_x - line_x1) * (line_y2 - line_y1) - (point_y - line_y1) * (line_x2 - line_x1)

# --- 🚀 AKHIR PERUBAHAN 1 ---

# Variabel Global untuk berbagi data antar thread
LOCATION_TRACKERS = {}
LATEST_DETECTION_STATS = {}


class SimpleObjectTracker:
    def __init__(self, max_disappeared=30, max_distance=50, initial_counts=None, fps=20):
        self.next_object_id = 0
        self.objects = {}
        self.disappeared = defaultdict(int)
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance
        # if initial_counts:
        #     self.total_counts = defaultdict(int, initial_counts)
        # else:
        #     self.total_counts = defaultdict(int)
        # self.session_counts = defaultdict(int)
        # TAMBAHKAN baris-baris ini:
        self.object_zone = defaultdict(int) # Key: obj_id, Value: Zona Terakhir
        self.counts_menuju_jauh = defaultdict(int) # Misal: Arah "Depok"
        self.counts_menuju_dekat = defaultdict(int) # Misal: Arah "Bogor"
        # --- 🚀 AKHIR PERUBAHAN 2 ---
        self.first_seen = defaultdict(float)
        self.counted = defaultdict(bool)
        self.last_moved = defaultdict(float)
        self.is_parked = defaultdict(bool)
        self.is_odol_logged = defaultdict(bool)
        self.plate_logged = defaultdict(bool)
        self.history = defaultdict(lambda: deque(maxlen=10))
        self.fps = fps 
        self.speed_history = defaultdict(lambda: deque(maxlen=5)) 
        src_pts = np.array([
            [370, 253], [463, 251], [480, 350], [373, 350]
        ], dtype=np.float32)
        REAL_WIDTH_M = 1.8 * 0.5
        REAL_LENGTH_M = 4.5 * 0.5
        output_width_px = int(REAL_WIDTH_M * 30)
        output_length_px = int(REAL_LENGTH_M * 30)
        dst_pts = np.array([[0, 0], [output_width_px, 0], [output_width_px, output_length_px], [0, output_length_px]], dtype=np.float32)
        self.perspective_matrix = cv2.getPerspectiveTransform(src_pts, dst_pts)
        self.ppm_birdseye = output_width_px / REAL_WIDTH_M

    def reset_completely(self):
        print(f"[{threading.current_thread().name}] RESETTING TRACKER COMPLETELY")
        self.next_object_id = 0
        self.objects.clear()
        self.disappeared.clear()
        self.total_counts.clear()
        self.session_counts.clear()
        self.history.clear()
        self.speed_history.clear()
        self.plate_logged.clear()
        self.is_parked.clear()
        self.is_odol_logged.clear()
        self.last_moved.clear()
        self.first_seen.clear()
        self.counted.clear()
        print(f"[{threading.current_thread().name}] Tracker reset done.")

    def calculate_distance(self, point1, point2):
        return math.sqrt((point1[0] - point2[0]) ** 2 + (point1[1] - point2[1]) ** 2)

    def register(self, detection):
        class_name = detection["class_name"]
        obj_id = self.next_object_id
        self.objects[obj_id] = {
            "center": detection["center"], "bbox": detection["bbox"],
            "class_name": class_name, "class_id": detection["class_id"],
            "confidence": detection["confidence"], "counted": False,
            "prev_center": detection["center"]
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
        for d in [self.objects, self.disappeared, self.history, self.speed_history, 
                  self.is_odol_logged, self.plate_logged, self.is_parked, 
                  self.last_moved, self.first_seen, self.counted]:
            d.pop(object_id, None)

    def update(self, detections):
        if len(detections) == 0:
            for object_id in list(self.disappeared.keys()):
                self.disappeared[object_id] += 1
                if self.disappeared[object_id] > self.max_disappeared:
                    self.deregister(object_id)
            # current_time = time.time()
            # for object_id in list(self.objects.keys()):
            #     if not self.counted.get(object_id, False):
            #         if current_time - self.first_seen.get(object_id, current_time) > 2.0:
            #             class_name = self.objects[object_id]["class_name"]
            #             self.total_counts[class_name] += 1
            #             self.session_counts[class_name] += 1
            #             self.counted[object_id] = True
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
                    if object_id in used_object_ids: continue
                    distance = self.calculate_distance(detection["center"], self.objects[object_id]["center"])
                    if distance < min_distance and distance < self.max_distance:
                        min_distance = distance
                        min_object_id = object_id
                if min_object_id is not None:
                    self.objects[min_object_id]["center"] = detection["center"]
                    self.objects[min_object_id]["bbox"] = detection["bbox"]
                    self.objects[min_object_id]["confidence"] = detection["confidence"]
                    self.disappeared[min_object_id] = 0
                    transformed_center = self.transform_point(detection["center"])
                    self.history[min_object_id].append(transformed_center)

                    obj_id = min_object_id
                    center_x, center_y = self.objects[obj_id]["center"]
                    class_name = self.objects[obj_id]["class_name"]

                    # Tentukan zona SEKARANG
                    (l1_x1, l1_y1), (l1_x2, l1_y2) = LINE_1_COORDS
                    (l2_x1, l2_y1), (l2_x2, l2_y2) = LINE_2_COORDS

                    # Kita asumsikan > 0 adalah "di atas" garis
                    # (Anda mungkin perlu mengubah > 0 dan < 0 ini setelah tes)
                    side_1 = get_point_side(center_x, center_y, l1_x1, l1_y1, l1_x2, l1_y2)
                    side_2 = get_point_side(center_x, center_y, l2_x1, l2_y1, l2_x2, l2_y2)
                    
                    current_zone = 0
                    if side_1 > 0:
                        current_zone = ZONE_JAUH   # Zona 1
                    elif side_1 < 0 and side_2 > 0:
                        current_zone = ZONE_TENGAH # Zona 2
                    elif side_2 < 0:
                        current_zone = ZONE_DEKAT  # Zona 3

                    previous_zone = self.object_zone[obj_id]

                    if current_zone != 0 and previous_zone != current_zone:
                        
                        # --- Arah: Menuju JAUH (Dekat -> Tengah -> Jauh) ---
                        if previous_zone == ZONE_DEKAT and current_zone == ZONE_TENGAH:
                            self.object_zone[obj_id] = ZONE_TENGAH
                        
                        elif previous_zone == ZONE_TENGAH and current_zone == ZONE_JAUH:
                            self.counts_menuju_jauh[class_name] += 1
                            self.object_zone[obj_id] = ZONE_JAUH
                            print(f"HITUNGAN (MENUJU JAUH) {class_name}: {self.counts_menuju_jauh[class_name]}")

                        # --- Arah: Menuju DEKAT (Jauh -> Tengah -> Dekat) ---
                        elif previous_zone == ZONE_JAUH and current_zone == ZONE_TENGAH:
                            self.object_zone[obj_id] = ZONE_TENGAH
                        
                        elif previous_zone == ZONE_TENGAH and current_zone == ZONE_DEKAT:
                            self.counts_menuju_dekat[class_name] += 1
                            self.object_zone[obj_id] = ZONE_DEKAT
                            print(f"HITUNGAN (MENUJU DEKAT) {class_name}: {self.counts_menuju_dekat[class_name]}")

                        elif previous_zone == 0:
                            self.object_zone[obj_id] = current_zone
                    
                    # --- 🚀 AKHIR PERUBAHAN 3.1 ---

                    used_detection_indices.add(detection_idx)
                    used_object_ids.add(min_object_id)

            for detection_idx, detection in enumerate(detections):
                if detection_idx not in used_detection_indices: self.register(detection)

            current_time = time.time()
            for object_id in object_ids:
                if object_id not in used_object_ids:
                    self.disappeared[object_id] += 1
                    if self.disappeared[object_id] > self.max_disappeared:
                        self.deregister(object_id)
                else:
                    current_center = self.objects[object_id]["center"]
                    prev_center = self.objects[object_id].get("prev_center", current_center)
                    move_dist = self.calculate_distance(current_center, prev_center)
                    if move_dist > 5:
                        self.last_moved[object_id] = current_time
                        self.is_parked[object_id] = False
                        self.is_odol_logged[object_id] = False
                        self.plate_logged[object_id] = False
                    self.objects[object_id]["prev_center"] = current_center
            
            # for object_id in list(self.objects.keys()):
            #     if not self.counted.get(object_id, False):
            #         if current_time - self.first_seen.get(object_id, current_time) > 2.0:
            #             class_name = self.objects[object_id]["class_name"]
            #             self.total_counts[class_name] += 1
            #             self.session_counts[class_name] += 1
            #             self.counted[object_id] = True
        return self.objects
    
    def transform_point(self, point):
        point_np = np.array([[[point[0], point[1]]]], dtype=np.float32)
        transformed_point = cv2.perspectiveTransform(point_np, self.perspective_matrix)
        return (transformed_point[0][0][0], transformed_point[0][0][1])

    def calculate_speed(self, object_id):
        if len(self.history[object_id]) < 2:
            self.speed_history[object_id].append(0)
            return 0.0
        p1_transformed = self.history[object_id][-2]
        p2_transformed = self.history[object_id][-1]
        dist_pixels = self.calculate_distance(p1_transformed, p2_transformed)
        dist_meters = dist_pixels / self.ppm_birdseye
        time_seconds = 1.0 / self.fps
        instant_speed_kph = (dist_meters / time_seconds) * 3.6
        MAX_SPEED_KPH = 100.0
        if instant_speed_kph > MAX_SPEED_KPH:
            if len(self.speed_history[object_id]) > 0:
                return np.mean(self.speed_history[object_id])
            else:
                return 0.0
        self.speed_history[object_id].append(instant_speed_kph)
        return np.mean(self.speed_history[object_id])

def reset_location_data(location_name):
    print(f"[{threading.current_thread().name}] STARTING COMPLETE RESET FOR LOCATION: {location_name}")
    if location_name in LOCATION_TRACKERS:
        LOCATION_TRACKERS[location_name].reset_completely()
        LOCATION_TRACKERS.pop(location_name, None)
    if location_name in LATEST_DETECTION_STATS:
        LATEST_DETECTION_STATS.pop(location_name, None)
    try:
        conn = sqlite3.connect("traffic_counting.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM counting_data WHERE location = ? AND datetime(timestamp) >= datetime('now', '-1 hour')", (location_name,))
        cursor.execute("DELETE FROM parking_violations WHERE location = ? AND datetime(timestamp) >= datetime('now', '-1 hour')", (location_name,))
        cursor.execute("DELETE FROM odol_detections WHERE location = ? AND datetime(timestamp) >= datetime('now', '-1 hour')", (location_name,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[{threading.current_thread().name}] Database cleanup error: {e}")
    print(f"[{threading.current_thread().name}] COMPLETE RESET FINISHED FOR: {location_name}")

def init_database():
    conn = sqlite3.connect("traffic_counting.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS counting_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME, location TEXT,
            person_total INTEGER DEFAULT 0, car_total INTEGER DEFAULT 0, 
            motorcycle_total INTEGER DEFAULT 0, bus_total INTEGER DEFAULT 0, grand_total INTEGER DEFAULT 0
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parking_violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME, location TEXT,
            vehicle_type TEXT, parked_duration_sec REAL, object_id INTEGER
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS crowd_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME, location TEXT,
            crowd_size INTEGER, duration_sec REAL
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS odol_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME, location TEXT,
            vehicle_type TEXT, aspect_ratio REAL, area REAL
        )""")
    conn.commit()
    conn.close()

def save_counting_data(location, counts):
    try:
        conn = sqlite3.connect("traffic_counting.db")
        cursor = conn.cursor()
        grand_total = sum(counts.values())
        cursor.execute("""
            INSERT INTO counting_data 
            (timestamp, location, person_total, car_total, motorcycle_total, bus_total, grand_total)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(), location, counts.get("person", 0), counts.get("car", 0),
            counts.get("motorcycle", 0), counts.get("bus", 0), grand_total,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[{threading.current_thread().name}] Database error (counting): {e}")

def save_parking_violation(location, vehicle_type, duration, obj_id):
    try:
        conn = sqlite3.connect("traffic_counting.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO parking_violations 
            (timestamp, location, vehicle_type, parked_duration_sec, object_id)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now(), location, vehicle_type, duration, obj_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[{threading.current_thread().name}] Database error (parking): {e}")

def save_crowd_detection(location, crowd_size, duration):
    try:
        conn = sqlite3.connect("traffic_counting.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO crowd_detections 
            (timestamp, location, crowd_size, duration_sec)
            VALUES (?, ?, ?, ?)
        """, (datetime.now(), location, crowd_size, duration))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[{threading.current_thread().name}] Database error (crowd): {e}")

def save_odol_detection(location, vehicle_type, aspect_ratio, area):
    try:
        conn = sqlite3.connect("traffic_counting.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO odol_detections 
            (timestamp, location, vehicle_type, aspect_ratio, area)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now(), location, vehicle_type, aspect_ratio, area))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[{threading.current_thread().name}] Database error (odol): {e}")

def detect_objects(frame, confidence_threshold=0.4, classes_to_detect=None):
    if model is None: return []
    try:
        results = model(frame, conf=confidence_threshold, classes=classes_to_detect, verbose=False)
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
                            "aspect_ratio": (y2 - y1) / (x2 - x1) if (x2 - x1) > 0 else 0,
                            "area": (x2 - x1) * (y2 - y1)
                        }
                        detections.append(detection)
        return detections
    except Exception as e:
        print(f"[{threading.current_thread().name}] Detection error: {e}")
        return []

def draw_bounding_boxes(frame, tracked_objects, tracker, location_name="unknown", crowd_member_ids=None, is_crowd=False):
    if crowd_member_ids is None:
        crowd_member_ids = set()
    output_frame = frame.copy()

    # --- 🚀 AWAL PERUBAHAN 4 ---
    (l1_x1, l1_y1), (l1_x2, l1_y2) = LINE_1_COORDS
    (l2_x1, l2_y1), (l2_x2, l2_y2) = LINE_2_COORDS
    
    # 1. Gambar 2 Garis
    cv2.line(output_frame, (l1_x1, l1_y1), (l1_x2, l1_y2), (0, 255, 255), 2) # Kuning
    cv2.line(output_frame, (l2_x1, l2_y1), (l2_x2, l2_y2), (0, 255, 255), 2) # Kuning

    # Di dalam fungsi draw_bounding_boxes, GANTI blok kode ARROW LAMA dengan ini:

    # ... (Setelah kode cv2.line untuk garis hitung) ...

    # 1. AMBIL UKURAN FRAME SAAT INI
    h, w, _ = output_frame.shape # Ambil lebar (w) dan tinggi (h) frame

    # Di dalam fungsi draw_bounding_boxes, GANTI blok kode ARROW LAMA dengan ini:

    # ... (Setelah kode cv2.line untuk garis hitung) ...

    # Di dalam fungsi draw_bounding_boxes, GANTI blok kode cv2.fillPoly LAMA dengan ini:

    # ... (Setelah kode cv2.line untuk garis hitung) ...

    # 2. TAMBAKKAN PANAH SEGITIGA PENUH (Pangkal di Garis)

    ARROW_SIZE = 15 # Ukuran piksel segitiga
    ARROW_COLOR_JAUH = (0, 255, 0) # Hijau
    ARROW_COLOR_DEKAT = (0, 0, 255) # Merah

    # --- PANAH 1: Menuju JAUH (UP/Left) - Pada Garis 1 (Y=198) ---
    # Pangkal segitiga di garis Y=198. Ujung menunjuk ke atas.
    triangle_jauh_coords = np.array([
        [200 - ARROW_SIZE, 198],    # Titik kiri pangkal (di garis)
        [200 + ARROW_SIZE, 198],    # Titik kanan pangkal (di garis)
        [200, 198 - ARROW_SIZE]     # Titik ujung atas
    ], np.int32)

    # Gambar segitiga penuh
    cv2.fillPoly(output_frame, [triangle_jauh_coords], ARROW_COLOR_JAUH)


    # --- PANAH 2: Menuju DEKAT (DOWN/Right) - Pada Garis 2 (Y=244) ---
    # Pangkal segitiga di garis Y=244. Ujung menunjuk ke bawah.
    triangle_dekat_coords = np.array([
        [450 - ARROW_SIZE, 244],    # Titik kiri pangkal (di garis)
        [450 + ARROW_SIZE, 244],    # Titik kanan pangkal (di garis)
        [450, 244 + ARROW_SIZE]     # Titik ujung bawah
    ], np.int32)

    # Gambar segitiga penuh
    cv2.fillPoly(output_frame, [triangle_dekat_coords], ARROW_COLOR_DEKAT)

# --- AKHIR PERBAIKAN ARROW ---

    # Di dalam draw_bounding_boxes, GANTI seluruh blok GRID lama dengan ini:

    # --- SETELAN UNTUK LAYOUT GRID (TABEL) ---
    COL_LABELS = ["Arah", "MOBIL", "MOTOR", "BUS", "TRUK"]
    START_X = 10
    START_Y = 12
    COL_WIDTH = 50    # DIKECILKAN (dari 60 ke 50) -> FIX RUANG KOSONG
    ROW_HEIGHT = 16
    FONT_SCALE = 0.35 
    FONT_THICKNESS = 1
    CENTER_OFFSET = 15 # Offset untuk memusatkan angka di kolom 50px
    TEXT_HEIGHT_PAD = 3 # Padding kecil di bawah baseline teks
    TEXT_BASELINE_Y = START_Y + 9 # Menetapkan Y agar teks pas di tengah baris
    
    TABLE_X_END = START_X + len(COL_LABELS) * COL_WIDTH 
    TABLE_Y_END = 5 + 3 * ROW_HEIGHT + 3 # 5 (Top Border) + 36 (3 Rows) + 3 (Bottom Padding) = 44
    BORDER_COLOR = (100, 100, 100)
    
    # ----------------------------------------------------
    # 1. GAMBAR LATAR BELAKANG HITAM PADAT
    # ----------------------------------------------------
    cv2.rectangle(output_frame, (5, 5), (TABLE_X_END, TABLE_Y_END), (0, 0, 0), -1) 

    # ----------------------------------------------------
    # 2. TAMBAHKAN GARIS GRID (BORDERS)
    # ----------------------------------------------------
    # Garis Horizontal
    cv2.line(output_frame, (5, START_Y + 4), (TABLE_X_END, START_Y + 4), BORDER_COLOR, FONT_THICKNESS) # Di bawah header
    cv2.line(output_frame, (5, START_Y + ROW_HEIGHT + 4), (TABLE_X_END, START_Y + ROW_HEIGHT + 4), BORDER_COLOR, FONT_THICKNESS) # Garis di tengah
    cv2.line(output_frame, (5, TABLE_Y_END), (TABLE_X_END, TABLE_Y_END), BORDER_COLOR, FONT_THICKNESS) # Garis paling bawah

    # Garis Vertikal
    for i in range(len(COL_LABELS) + 1):
        x_pos = START_X + i * COL_WIDTH
        cv2.line(output_frame, (x_pos, 5), (x_pos, TABLE_Y_END), BORDER_COLOR, FONT_THICKNESS)

    # ----------------------------------------------------
    # 3. GAMBAR TEKS & DATA (Looping)
    # ----------------------------------------------------
    
    # 3a. GAMBAR HEADER (ROW 1 - Rata Kiri)
    Y_HEADER_TEXT = START_Y
    for i, label in enumerate(COL_LABELS):
        x_pos = START_X + i * COL_WIDTH
        cv2.putText(output_frame, label, (x_pos + 3, Y_HEADER_TEXT), cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE, (255, 255, 255), FONT_THICKNESS)

    # 3b. GAMBAR DATA ARAH ATAS (ROW 2 - Rata Tengah)
    Y_ROW_ATAS = START_Y + ROW_HEIGHT
    counts_jauh = tracker.counts_menuju_jauh 
    cv2.putText(output_frame, "ATAS", (START_X + 3, Y_ROW_ATAS + TEXT_HEIGHT_PAD), cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE, (0, 255, 0), FONT_THICKNESS) 
    categories = ['car', 'motorcycle', 'bus', 'truck']
    for i, category in enumerate(categories):
        # Tambahkan offset 15 untuk memusatkan angka di kolom 50px
        x_pos = START_X + (i + 1) * COL_WIDTH + CENTER_OFFSET 
        count_val = counts_jauh.get(category, 0)
        cv2.putText(output_frame, str(count_val), (x_pos, Y_ROW_ATAS + TEXT_HEIGHT_PAD), cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE, (0, 255, 0), FONT_THICKNESS)

    # 3c. GAMBAR DATA ARAH BAWAH (ROW 3 - Rata Tengah)
    Y_ROW_BAWAH = START_Y + 2 * ROW_HEIGHT
    counts_dekat = tracker.counts_menuju_dekat
    cv2.putText(output_frame, "BAWAH", (START_X + 3, Y_ROW_BAWAH + TEXT_HEIGHT_PAD), cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE, (0, 255, 255), FONT_THICKNESS) 
    for i, category in enumerate(categories):
        # Tambahkan offset 15 untuk memusatkan angka di kolom 50px
        x_pos = START_X + (i + 1) * COL_WIDTH + CENTER_OFFSET
        count_val = counts_dekat.get(category, 0)
        cv2.putText(output_frame, str(count_val), (x_pos, Y_ROW_BAWAH + TEXT_HEIGHT_PAD), cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE, (0, 255, 255), FONT_THICKNESS)

    if is_crowd:
        #  cv2.putText(output_frame, "KERUMUNAN", (20, 40),
        #             cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLORS["crowd"], 2)
        pass

    for object_id, obj in tracked_objects.items():
        bbox = obj["bbox"]
        class_name = obj["class_name"]
        x, y, w, h = bbox
        
        color = COLORS.get(class_name, (255, 255, 255)) 
        label_text = f"{object_id}"
        
        if class_name == "person" and object_id in crowd_member_ids:
            color = COLORS["crowd"]
        
        if tracker:
            if tracker.is_parked.get(object_id, False):
                color = COLORS["parking"]
                label_text = "PARKIR LIAR"
            elif tracker.is_odol_logged.get(object_id, False):
                color = COLORS["odol"]
                label_text = "ODOL"
            
            if class_name in ["car", "motorcycle", "bus"] and "KAPTEN MUSLIHAT" in location_name.upper():
                 speed_kph = tracker.calculate_speed(object_id)
                 label_text = f"{object_id} [{speed_kph:.1f} kmh]"
                 if tracker.is_parked.get(object_id, False):
                     label_text = "PARKIR LIAR"

        font_scale = 0.28
        padding = 2
        font_thickness = 1
        (text_width, text_height), baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)
        label_bg_y1 = y - text_height - padding * 2
        label_bg_y2 = y 
        if label_bg_y1 < 0:
            label_bg_y1 = y
            label_bg_y2 = y + text_height + padding * 2
        
        cv2.rectangle(output_frame, (x, label_bg_y1), (x + text_width + padding, label_bg_y2), color, -1)
        cv2.putText(output_frame, label_text, (x + int(padding/2), y - padding), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), font_thickness)
        cv2.rectangle(output_frame, (x, y), (x + w, y + h), color, 1)

    return output_frame

def get_stream_from_m3u8(m3u8_url, max_retries=3):
    for attempt in range(max_retries):
        try:
            cap = cv2.VideoCapture(m3u8_url)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ret, test_frame = cap.read()
            if ret and test_frame is not None:
                print(f"[{threading.current_thread().name}] Stream connected successfully: {m3u8_url}")
                return cap
            else:
                cap.release()
        except Exception as e:
            print(f"[{threading.current_thread().name}] Stream connection attempt {attempt + 1} failed: {e}")
            try:
                if cap: cap.release()
            except: pass
            if attempt < max_retries - 1:
                time.sleep(2)
    print(f"[{threading.current_thread().name}] Failed to connect to stream after {max_retries} attempts")
    return None

def recognize_plate(frame, plate_bbox):
    if READER_OCR is None: return "OCR Gagal"
    x, y, w, h = plate_bbox 
    x1, y1, x2, y2 = x, y, x + w, y + h
    h_frame, w_frame, _ = frame.shape
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = min(w_frame, x2); y2 = min(h_frame, y2)
    plate_crop = frame[y1:y2, x1:x2]
    if plate_crop.size == 0: return "Area Kosong"
    try:
        results = READER_OCR.readtext(plate_crop)
        if results:
            plate_text = results[0][1] 
            clean_text = plate_text.upper().replace(' ', '').replace('.', '')
            return clean_text
        else:
            return "Tidak Terdeteksi"
    except Exception as e:
        print(f"[{threading.current_thread().name}] EasyOCR error: {e}")
        return "OCR Error"

# vvvvvvvvvvvv INI FUNGSI YANG HILANG vvvvvvvvvvvv
def detect_crowd(tracked_objects, min_crowd_size=5, crowd_radius_threshold=100):
    """
    Mendeteksi kerumunan dari objek 'person' yang terlacak.
    Mengembalikan (True/False, set_of_crowd_member_ids)
    """
    # Filter hanya 'person'
    people_objects = {oid: obj for oid, obj in tracked_objects.items() if obj["class_name"] == "person"}
    
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
            dist = math.sqrt((centers[i][0] - centers[j][0])**2 + (centers[i][1] - centers[j][1])**2)
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
# ^^^^^^^^^^^^ AKHIR FUNGSI TAMBAHAN ^^^^^^^^^^^^


# -------------------------------------------------------------------
# FUNGSI WORKER (OTAK DETEKSI 24/7) - VERSI TANGGUH
# -------------------------------------------------------------------
def run_detection_worker(stream_url, location_name, detection_mode="all", stop_event=None):
    
    thread_name = threading.current_thread().name
    print(f"[{thread_name}] WORKER DIMULAI: Memulai deteksi untuk: {location_name}")

    # Inisialisasi awal. Akan di-reset jika worker benar-benar berhenti
    reset_location_data(location_name)
    init_database()

    # Buat entri stats sekali saja. 
    # generate_frames akan menampilkan 'latest_frame' (gambar hitam) dari sini
    # sampai worker berhasil terhubung dan memperbaruinya.
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
            "stat_total_pelanggaran": 0
        }

    tracker = SimpleObjectTracker(max_disappeared=80, max_distance=280, fps=10)
    LOCATION_TRACKERS[location_name] = tracker

    # Hapus koneksi awal dari sini. Kita pindahkan ke dalam loop.
    
    frame_count = 0
    crowd_currently_logged = False
    is_crowd = False
    
    session_parking_count = 0
    session_odol_count = 0
    
    cap = None # Inisialisasi cap sebagai None

    try:
        # Ini adalah loop abadi. 
        # Worker akan terus hidup dan mencoba menyambung ulang selamanya.
        while True:
            # --- Kill Switch Check ---
            if stop_event and stop_event.is_set():
                    # PASTIKAN KEDUA BARIS INI DIBERI INDENTASI
                    # BARIS 764
                    print(f"[{thread_name}] WORKER INFO: Menerima sinyal berhenti. Keluar dari loop.")
                    # BARIS 765
                    break # Keluar dari loop utama
        # --- End Kill Switch Check ---
            
            # --- BLOK KONEKSI BARU ---
            # Jika stream tidak terhubung (cap is None), coba sambungkan.
            if cap is None:
                print(f"[{thread_name}] WORKER INFO: Mencoba koneksi ke stream {location_name}...")
                cap = get_stream_from_m3u8(stream_url)
                
                if cap is None:
                    continue 
                else:
                    print(f"[{thread_name}] WORKER INFO: Koneksi {location_name} berhasil.")
                    
                    try:
                        socketio.emit('stream_ready', {
                            'location': location_name,
                            'status': 'ready'
                        })
                    except Exception as e:
                        print(f"[{thread_name}] SocketIO emit error (ready): {e}")
 
            # --- AKHIR BLOK KONEKSI BARU ---

            # Jika kita sampai di sini, cap PASTI sudah terhubung.
            
            start_time = time.time()
            ret, frame = cap.read()
            
            # Jika stream terputus di tengah jalan
            if not ret or frame is None:
                print(f"[{thread_name}] WORKER ERROR: Stream {location_name} terputus. Menutup koneksi lama...")
                cap.release()
                cap = None # Set cap ke None agar blok koneksi di atas berjalan lagi
                time.sleep(5)
                continue # Ulangi loop, coba sambung lagi

             # --- MULAI DETEKSI (KODE ANDA YANG SEBELUMNYA) ---
            if frame.shape[1] > 600:
                h_orig, w_orig = frame.shape[:2]
                frame = cv2.resize(frame, (600, int(h_orig * 600 / w_orig)))

            detections = []
            tracked_objects = {}

            if frame_count % 8 == 0:
                detections = detect_objects(frame, confidence_threshold=0.5, classes_to_detect=[0, 2, 3, 5])
            
            tracked_objects = tracker.update(detections)

            # --- LOGIKA DETEKSI ---
            
            is_crowd, crowd_ids = detect_crowd(tracked_objects, min_crowd_size=2, crowd_radius_threshold=40)

            if is_crowd and not crowd_currently_logged:
                current_time = time.time()
                first_seen_time = tracker.first_seen.get(list(crowd_ids)[0], current_time) if crowd_ids else current_time
                duration = current_time - first_seen_time
                save_crowd_detection(location_name, len(crowd_ids), duration)
                crowd_currently_logged = True
                print(f"[{thread_name}] NOTIFIKASI: Kerumunan terdeteksi di {location_name}")
                try:
                    socketio.emit('notifikasi_baru', {
                        'title': '🚨 Kerumunan Terdeteksi!',
                        'detail': f'Terdeteksi {len(crowd_ids)} orang berkumpul di {location_name}.',
                        'icon': 'warning', 'location': location_name
                    })
                except Exception as e: print(f"[{thread_name}] SocketIO emit error (crowd): {e}")
            
            elif not is_crowd:
                crowd_currently_logged = False

            current_time = time.time()
            for obj_id, obj in tracked_objects.items():
                if obj["class_name"] in ["car", "motorcycle", "bus"]:
                    
                    last_move = tracker.last_moved.get(obj_id, current_time)
                    parked_duration = current_time - last_move
                    was_parked = tracker.is_parked.get(obj_id, False)
                    now_parked = parked_duration > 50
                    
                    if not was_parked and now_parked:
                        save_parking_violation(location_name, obj["class_name"], parked_duration, obj_id)
                        session_parking_count += 1
                        print(f"[{thread_name}] NOTIFIKASI: Parkir liar terdeteksi di {location_name}")
                        try:
                            socketio.emit('notifikasi_baru', {
                                'title': '🅿️ Parkir Liar Terdeteksi!',
                                'detail': f'Satu {obj["class_name"]} parkir liar di {location_name} (ID: {obj_id}).',
                                'icon': 'error', 'location': location_name
                            })
                        except Exception as e: print(f"[{thread_name}] SocketIO emit error (parking): {e}")
                    tracker.is_parked[obj_id] = now_parked

                    if obj["class_name"] == "bus":
                        aspect_ratio = obj.get("aspect_ratio", 0)
                        area = obj["bbox"][2] * obj["bbox"][3] 
                        is_odol = aspect_ratio > 0.8 or area > 10000 
                        was_odol_logged = tracker.is_odol_logged.get(obj_id, False)

                        if is_odol and not was_odol_logged:
                            save_odol_detection(location_name, obj["class_name"], aspect_ratio, area)
                            tracker.is_odol_logged[obj_id] = True
                            session_odol_count += 1
                            print(f"[{thread_name}] NOTIFIKASI: ODOL terdeteksi di {location_name}")
                            try:
                                socketio.emit('notifikasi_baru', {
                                    'title': '🚚 Deteksi ODOL!',
                                    'detail': f'Kendaraan {obj["class_name"]} (ID: {obj_id}) terindikasi ODOL di {location_name}.',
                                    'icon': 'info', 'location': location_name
                                })
                            except Exception as e: print(f"[{thread_name}] SocketIO emit error (odol): {e}")
                    
                    # x, y, w, h = obj["bbox"]
                    # plate_x_offset = int(w * 0.25); plate_y_offset = int(h * 0.70)
                    # plate_width = int(w * 0.50); plate_height = int(h * 0.20)
                    # plate_x = x + plate_x_offset; plate_y = y + plate_y_offset
                    # plate_bbox_crop = [plate_x, plate_y, plate_width, plate_height] 
                    
                    # recognized_plate = recognize_plate(frame, plate_bbox_crop)

                    # if recognized_plate != "Tidak Terdeteksi" and recognized_plate != "OCR Error":
                    #     was_plate_logged = tracker.plate_logged.get(obj_id, False)
                    #     if not was_plate_logged:
                    #         print(f"[{thread_name}] NOTIFIKASI: Plat {recognized_plate} terdeteksi di {location_name}")
                    #         try:
                    #             socketio.emit('notifikasi_baru', {
                    #                 'title': 'ℹ️ Plat Nomor Terbaca',
                    #                 'detail': f'Plat {recognized_plate} (ID: {obj_id}) terdeteksi di {location_name}.',
                    #                 'icon': 'info', 'location': location_name
                    #             })
                    #             tracker.plate_logged[obj_id] = True
                    #         except Exception as e: print(f"[{thread_name}] SocketIO emit error (plate): {e}")
            
            frame_count += 1

            output_frame = draw_bounding_boxes(
                frame, 
                tracked_objects, 
                tracker, 
                location_name=location_name, 
                crowd_member_ids=crowd_ids,
                is_crowd=is_crowd
            )

            # total_counts = dict(tracker.total_counts)

            counts_jauh = dict(tracker.counts_menuju_jauh)
            counts_dekat = dict(tracker.counts_menuju_dekat)
            
            active_parked_count = sum(1 for parked in tracker.is_parked.values() if parked)
            active_odol_count = sum(1 for odol in tracker.is_odol_logged.values() if odol)
            active_crowd_count = 1 if is_crowd else 0
            session_peringatan_aktif = active_parked_count + active_odol_count + active_crowd_count

            # Ini adalah bagian yang mengirim frame ke generate_frames
            # Kita perbarui LATEST_DETECTION_STATS dengan frame yang sudah digambar
            # --- PERBAIKAN: Encode frame ke JPEG secara manual ---
            ret_enc, jpeg_buffer = cv2.imencode('.jpg', output_frame)
            frame_bytes_untuk_dikirim = jpeg_buffer.tobytes() if ret_enc else None
            # --- AKHIR PERBAIKAN ---
            current_stats = {
                "latest_frame": frame_bytes_untuk_dikirim, # <-- GUNAKAN HASIL ENCODE
                "current_tracked_objects": tracked_objects,
                "crowd_member_ids": list(crowd_ids), # <-- UBAH DARI SET KE LIST
                "is_crowd_detected": is_crowd,
                # "total_counts": total_counts,
                "counts_jauh": counts_jauh,
                "counts_dekat": counts_dekat,

                "stat_parkir": session_parking_count,
                "stat_odol": session_odol_count,
                "stat_peringatan_aktif": session_peringatan_aktif,
                "stat_total_pelanggaran": session_parking_count + session_odol_count
            }
            
            LATEST_DETECTION_STATS[location_name] = current_stats

            if frame_count % 10 == 0:
                try:
                    # stats_packet = {k: v for k, v in current_stats.items() if k != 'latest_frame' and k != 'current_tracked_objects'}
                    stats_packet = {
                    'location': location_name,
                    'is_crowd_detected': is_crowd,
                    
                    'counts_jauh_mobil': counts_jauh.get('car', 0),
                    'counts_jauh_motor': counts_jauh.get('motorcycle', 0),
                    'counts_jauh_bus': counts_jauh.get('bus', 0),
                    'counts_jauh_orang': counts_jauh.get('person', 0),
                    
                    'counts_dekat_mobil': counts_dekat.get('car', 0),
                    'counts_dekat_motor': counts_dekat.get('motorcycle', 0),
                    'counts_dekat_bus': counts_dekat.get('bus', 0),
                    'counts_dekat_orang': counts_dekat.get('person', 0),
                    
                    'stat_parkir': session_parking_count,
                    'stat_odol': session_odol_count,

                    }
                    
                    socketio.emit('update_stats', stats_packet)
                except Exception as e:
                    print(f"[{thread_name}] SocketIO emit error (stats): {e}")

            end_time = time.time()
            elapsed_time = end_time - start_time
            target_fps = 10
            target_delay = 1.0 / target_fps
            if elapsed_time < target_delay:
                time.sleep(target_delay - elapsed_time)

    except Exception as e:
        print(f"[{thread_name}] Error Kritis di WORKER {location_name}: {e}")
    finally:
        # Ini akan dieksekusi baik saat Error Kritis maupun saat Kill Switch
        if cap:
            cap.release()
            
        # --- PEMBERSIHAN MUTLAK (Anti-Persistensi) ---
        if location_name in LOCATION_TRACKERS:
            del LOCATION_TRACKERS[location_name]
        if location_name in LATEST_DETECTION_STATS:
            # Ini memastikan worker yang lama tidak meninggalkan frame atau stats di global state
            del LATEST_DETECTION_STATS[location_name] 
        # --- AKHIR PERBAIKAN ---
        
        print(f"[{thread_name}] WORKER {location_name} telah berhenti KARENA ERROR KRITIS.")
# -------------------------------------------------------------------
# FUNGSI STREAMER (HANYA MENGAMBIL FRAME DARI WORKER)
# -------------------------------------------------------------------
# -------------------------------------------------------------------
# FUNGSI STREAMER (HANYA MENGAMBIL FRAME DARI WORKER)
# -------------------------------------------------------------------
def generate_frames(stream_url, location_name, detection_mode="simple"):
    """
    Fungsi ini HANYA mengambil frame video yang sudah digambar oleh worker
    dari LATEST_DETECTION_STATS dan menayangkannya ke browser.
    """
    location_name = str(location_name).strip()
    print(f"[{threading.current_thread().name}] STREAM DIMULAI: Menyalakan video untuk: {location_name}")

    error_frame_bytes = None
    try:
        # Buat frame error sekali saja
        error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(error_frame, "Stream tidak tersedia", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
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
                frame_data = stats.get("latest_frame") # Ini bisa np.array ATAU bytes
                
                if isinstance(frame_data, np.ndarray):
                    # KASUS 1: Ini adalah frame inisialisasi (np.zeros)
                    # Kita harus encode manual
                    ret, buffer = cv2.imencode(".jpg", frame_data)
                    if ret:
                        frame_to_yield = buffer.tobytes()
                
                elif isinstance(frame_data, bytes):
                    # KASUS 2: Ini adalah frame yang sudah diproses (sudah .tobytes())
                    frame_to_yield = frame_data
                
                # Jika frame_to_yield berhasil disiapkan
                if frame_to_yield:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_to_yield + b"\r\n")
                else:
                    # Gagal memproses frame_data, kirim frame error
                    if error_frame_bytes:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + error_frame_bytes + b"\r\n")
            
            else:
                # KASUS 3: Worker belum jalan atau sudah mati
                if error_frame_bytes:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + error_frame_bytes + b"\r\n")

            time.sleep(1.0 / 20) # Target 20 FPS

    except GeneratorExit:
        print(f"[{threading.current_thread().name}] STREAM DIHENTIKAN: Klien menutup koneksi untuk {location_name}.")
    except Exception as e:
        print(f"[{threading.current_thread().name}] Error di generate_frames (streaming) untuk {location_name}: {e}")
    finally:
        print(f"[{threading.current_thread().name}] Sesi STREAMING untuk {location_name} selesai.")

def get_counting_summary(location, hours=24):
    pass

def generate_frames_preview_only(stream_url):
    pass
    
if __name__ == "__main__":
    init_database()
    print("Database initialized for simple counting")

