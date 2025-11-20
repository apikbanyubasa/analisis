import cv2
import numpy as np

# --- PENGATURAN ---
IMAGE_PATH = 'live_cctv.jpg' # Pastikan ini nama screenshot Anda
TARGET_WIDTH = 600 # SAMAKAN DENGAN DI ANALYZER.PY
# --- AKHIR PENGATURAN ---

img_display = None
original_img = None
resize_ratio = 1.0

def print_coords(event, x, y, flags, param):
    """Callback function saat mouse di-klik"""
    global img_display
    
    if event == cv2.EVENT_LBUTTONUP:
        
        # --- PERUBAHAN DI SINI ---
        # Kita tidak perlu menghitung ulang. (x,y) adalah koordinat
        # yang benar untuk gambar 600px kita.
        print(f"Koordinat BARU (untuk 600px): ({x}, {y})")
        # --- AKHIR PERUBAHAN ---
        
        # Gambar lingkaran merah di titik yang Anda klik
        cv2.circle(img_display, (x, y), 3, (0, 0, 255), -1) 
        cv2.imshow("Klik untuk Koordinat (RESIZED 600px)", img_display)

# 1. Muat gambar
original_img = cv2.imread(IMAGE_PATH)
if original_img is None:
    print(f"ERROR: Gagal memuat gambar dari '{IMAGE_PATH}'")
else:
    # 2. --- PERUBAHAN DI SINI: RESIZE GAMBAR ---
    h, w = original_img.shape[:2]
    
    # Hitung rasio dan tinggi baru, persis seperti di analyzer.py
    resize_ratio = TARGET_WIDTH / w
    new_h = int(h * resize_ratio)
    
    # Resize gambar ke 600px
    img_display = cv2.resize(original_img, (TARGET_WIDTH, new_h))
    # --- AKHIR PERUBAHAN ---

    # 3. Buat jendela dan pasang callback
    cv2.namedWindow("Klik untuk Koordinat (RESIZED 600px)")
    cv2.setMouseCallback("Klik untuk Koordinat (RESIZED 600px)", print_coords)

    print("--- Alat Pencari Koordinat (x,y) ---")
    print(f"Gambar telah di-resize ke lebar {TARGET_WIDTH}px.")
    print("\n1. Buka jendela gambar yang muncul (sekarang lebih kecil).")
    print("2. Klik di ujung-ujung 2 garis Anda.")
    print("3. Catat koordinat BARU (angka kecil) yang muncul di terminal ini.")
    print("4. Tekan tombol 'q' di jendela gambar jika sudah selesai.")
    
    cv2.imshow("Klik untuk Koordinat (RESIZED 600px)", img_display)
    
    while True:
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    
    cv2.destroyAllWindows()