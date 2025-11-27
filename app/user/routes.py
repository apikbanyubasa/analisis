from flask import Blueprint, render_template, jsonify, Response, url_for
from jinja2 import TemplateNotFound
import pandas as pd
import json
import math
from ..models import CCTV, BatasWilayah

# Diasumsikan Anda memiliki file analyzer.py di root atau di lokasi yang bisa diimpor
# Jika file ini tidak ada, Anda perlu membuatnya atau menghapus impor & fungsionalitas terkait
try:
    from analyzer import (
        generate_frames,
        LATEST_DETECTION_STATS,
        generate_frames_preview_only,
    )
except ImportError:
    # Fallback jika analyzer tidak ada, agar aplikasi tidak crash
    print("WARNING: analyzer.py not found. Detection features will be disabled.")
    generate_frames = None
    LATEST_DETECTION_STATS = {}
    generate_frames_preview_only = None


# Import model dari database
from ..models import CCTV, BatasWilayah

# --- Definisi Blueprint ---
user_bp = Blueprint(
    "user", __name__, template_folder="templates/user", static_folder="static"
)


def convert_video_url(url: str):
    """Mengonversi URL video YouTube atau restreamer ke format embed yang benar."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if "youtube.com/watch?v=" in url:
        try:
            video_id = url.split("watch?v=")[1].split("&")[0]
            return f"https://www.youtube.com/embed/{video_id}?autoplay=1&mute=1&loop=1&playlist={video_id}&controls=1"
        except IndexError:
            return None
    elif "youtube.com/embed/" in url:
        base_url = url.split("?")[0]
        video_id = base_url.split("/")[-1]
        return f"{base_url}?autoplay=1&mute=1&loop=1&playlist={video_id}&controls=1"
    elif url.lower().endswith(".html"):
        if "?" not in url:
            return f"{url}?autoplay=1&mute=1&controls=1"
        elif "controls" not in url:
            return f"{url}&autoplay=1&mute=1&controls=1"
        else:
            return url
    return None


# Fungsi helper untuk membalik koordinat
def swap_coords(coords):
    if isinstance(coords, list):
        if (
            len(coords) == 2
            and isinstance(coords[0], (int, float))
            and isinstance(coords[1], (int, float))
        ):
            # Ini adalah pasangan koordinat [lat, lon], balik menjadi [lon, lat]
            return [coords[1], coords[0]]
        else:
            # Lanjutkan rekursi ke dalam list
            return [swap_coords(c) for c in coords]
    return coords



@user_bp.route("/dashboard")
def dashboard():
    all_cctv = CCTV.query.order_by(CCTV.lokasi).all()
    cctv_data_for_js = [
        {
            "index": c.id,
            "lokasi": c.lokasi,
            "status": c.status,
            "type": c.type,
            "camera_type": c.camera_type,
            "latitude": c.latitude,
            "longitude": c.longitude,
            "video_url": convert_video_url(c.video_url),
            "stream_url": c.stream_url,
        }
        for c in all_cctv
    ]
    featured_cctv = CCTV.query.filter(
        CCTV.status.ilike("aktif"),
        CCTV.stream_url.isnot(None),
    ).first()
    featured_cctv_idx = featured_cctv.id if featured_cctv else None
    featured_cctv_lokasi = featured_cctv.lokasi if featured_cctv else None

    return render_template(
        "dashboard.html",
        featured_cctv_idx=featured_cctv_idx,
        featured_cctv_lokasi=featured_cctv_lokasi,
        cctv_data=cctv_data_for_js,
    )


# Bagian yang diperbaiki di routes.py


@user_bp.route("/cctv")
def cctv():
    # --- 1. Ambil Data CCTV dari Database ---
    cctv_db = CCTV.query.all()
    cctv_markers = []

    for c in cctv_db:

        # --- PERBAIKAN KRITIS: SANITASI KOORDINAT ---

        # Fungsi helper untuk membersihkan data koordinat
        def sanitize_coordinate(coord):
            if coord is None:
                return 0.0
            try:
                # Coba konversi ke float (penting jika disimpan sebagai string)
                f_coord = float(coord)
                # Cek jika nilai yang dikonversi adalah NaN (dari import CSV sebelumnya)
                if math.isnan(f_coord):
                    return 0.0
                return f_coord
            except (ValueError, TypeError):
                return 0.0  # Jika tidak bisa diubah ke float

        lat_safe = sanitize_coordinate(c.latitude)
        lng_safe = sanitize_coordinate(c.longitude)

        # JANGAN SERTAKAN MARKER JIKA KEDUA KOORDINAT ADALAH 0.0 (Data hilang/default)
        if lat_safe == 0.0 and lng_safe == 0.0:
            continue

        # --- AKHIR PERBAIKAN SANITASI ---

        converted_video_url = convert_video_url(c.video_url)

        # Siapkan konten video atau placeholder
        video_content = ""
        is_active_with_video = c.status.lower() == "aktif" and converted_video_url

        if is_active_with_video:
            # Jika aktif dan punya video, buat iframe
            video_content = f"""
                <iframe 
                    width="100%" height="150" src="{converted_video_url}" 
                    frameborder="0" allow="autoplay; encrypted-media" 
                    allowfullscreen>
                </iframe>
            """
        else:
            # Jika tidak aktif atau tidak punya video, tampilkan pesan
            video_content = """
                <div style="width:100%; height:150px; background:#000; color:white; display:flex; align-items:center; justify-content:center; text-align:center; font-size:14px;">
                    Preview video tidak tersedia.
                </div>
            """

        # Buat struktur HTML lengkap untuk popup
        popup_html = f"""
            <div style="width:250px;">
                <h4 style="font-weight:bold; margin:0 0 5px 0; font-size:16px;">{c.lokasi}</h4>
                <p style="margin:0 0 10px 0;">
                    Status: 
                    <span style="font-weight:bold; color:{'#28a745' if c.status.lower() == 'aktif' else '#dc3545'};">
                        {c.status}
                    </span>
                </p>
                {video_content}
            </div>
        """

        cctv_markers.append(
            {
                "id": c.id,
                "latitude": lat_safe,  # <--- Gunakan nilai yang sudah dibersihkan
                "longitude": lng_safe,  # <--- Gunakan nilai yang sudah dibersihkan
                "popup_content": popup_html,
                "lokasi": c.lokasi,
                "status": c.status,
                "type": c.type,
                "video_url": converted_video_url,
                "img_placeholder": url_for("static", filename="img/no-feed.svg"),
            }
        )

    # (Sisa kode untuk batas wilayah tetap sama)
    batas_wilayah_db = BatasWilayah.query.all()
    list_fitur_kecamatan = []
    for bw in batas_wilayah_db:
        if not bw.geojson or not bw.geojson.strip():
            continue
        try:
            geo_data = json.loads(bw.geojson)
            original_coords = geo_data.get("coordinates")
            # Pastikan swap_coords tersedia
            swapped_coords = swap_coords(original_coords)
            correct_geometry = {
                "type": geo_data.get("type", "MultiPolygon"),
                "coordinates": swapped_coords,
            }
            feature = {
                "type": "Feature",
                "properties": {"name": bw.nama, "type": bw.jenis},
                "geometry": correct_geometry,
            }
            list_fitur_kecamatan.append(feature)
        except Exception as e:
            print(f"Error processing GeoJSON for {bw.nama}: {e}")

    batas_wilayah_data = {"type": "FeatureCollection", "features": list_fitur_kecamatan}

    preview_cctvs_query = (
        CCTV.query.filter(
            CCTV.status.ilike("aktif"),
            CCTV.stream_url.isnot(None),
            CCTV.stream_url != "",
        )
        .limit(2)
        .all()
    )

    return render_template(
        "cctv.html",
        cctv_markers=cctv_markers,
        batas_wilayah_data=batas_wilayah_data,
        preview_cctvs=preview_cctvs_query,
    )


# Di dalam app/user/routes.py

@user_bp.route("/")
@user_bp.route("/kepadatan")  # <-- Ini adalah URL baru Anda
def kepadatan():
    """
    Menampilkan halaman Kepadatan. Logika ini sama dengan dashboard
    karena menggunakan data CCTV yang sama.
    """
    try:
        # 1. Ambil semua data CCTV dari database
        all_cctv = CCTV.query.order_by(CCTV.lokasi).all()

        # 2. Siapkan data CCTV untuk digunakan di JavaScript
        cctv_data_for_js = [
            {
                "index": c.id,
                "lokasi": c.lokasi,
                "status": c.status,
                "type": c.type,
                "camera_type": c.camera_type,
                "latitude": c.latitude,
                "longitude": c.longitude,
                "video_url": convert_video_url(c.video_url),
                "stream_url": c.stream_url,
            }
            for c in all_cctv
        ]

        # 3. Cari CCTV unggulan (featured) yang aktif dan mendukung deteksi
        featured_cctv = CCTV.query.filter(
            CCTV.status.ilike("aktif"),
            CCTV.stream_url.isnot(None),
        ).first()
        featured_cctv_idx = featured_cctv.id if featured_cctv else None
        featured_cctv_lokasi = featured_cctv.lokasi if featured_cctv else None

        # 4. Render template HTML yang baru
        return render_template(
            "kepadatan.html",  # <-- NAMA FILE HTML BARU
            featured_cctv_idx=featured_cctv_idx,
            featured_cctv_lokasi=featured_cctv_lokasi,
            cctv_data=cctv_data_for_js,
            title="Analisis Kepadatan Lalu Lintas",
        )
    except Exception as e:
        print(f"ERROR: Gagal memuat data untuk kepadatan: {e}")
        return render_template(
            "kepadatan.html",
            featured_cctv_idx=None,
            featured_cctv_lokasi="Data Tidak Ditemukan",
            cctv_data=[],
            title="Analisis Kepadatan Lalu Lintas",
        )


@user_bp.route("/api/cctv/<int:cctv_id>/info")
def get_cctv_info(cctv_id: int):
    cctv = CCTV.query.get_or_404(cctv_id)
    return jsonify(
        {
            "index": cctv.id,
            "lokasi": cctv.lokasi,
            "status": cctv.status,
            "type": cctv.type,
            "camera_type": cctv.camera_type,
            "latitude": cctv.latitude,
            "longitude": cctv.longitude,
            "video_url": convert_video_url(cctv.video_url),
            "stream_url": cctv.stream_url,
            "has_detection_stream": bool(cctv.stream_url),
        }
    )

@user_bp.route("/api/cctv/<int:cctv_id>/detection_status")
def get_detection_status(cctv_id: int):
    cctv = CCTV.query.get_or_404(cctv_id)
    supports_detection = (
        cctv.stream_url
        and cctv.status.lower() == "aktif"
    )
    return jsonify(
        {
            "camera_idx": cctv.id,
            "supports_detection": supports_detection,
            "stream_url": cctv.stream_url,
            "status": cctv.status,
            "location": cctv.lokasi,
        }
    )


@user_bp.route("/api/scan/<int:cctv_id>/data")
def get_scan_data(cctv_id: int):
    cctv = CCTV.query.get_or_404(cctv_id)
    stats = LATEST_DETECTION_STATS.get(cctv.lokasi.upper())
    if stats and "total_counts" in stats:
        detection_counts = stats["total_counts"]
    else:
        detection_counts = {"person": 0, "car": 0, "motorcycle": 0, "bus": 0}
    return jsonify(
        {
            "location": cctv.lokasi,
            "detections": detection_counts,
            "status": cctv.status.lower(),
        }
    )


@user_bp.route("/analyze_feed/<int:cctv_id>")
def analyze_feed(cctv_id: int):
    cctv = CCTV.query.get_or_404(cctv_id)
    cctv_info = {
        "index": cctv.id,
        "lokasi": cctv.lokasi,
        "status": cctv.status,
        "stream_url": cctv.stream_url,
        "supports_detection": bool(
            cctv.status.lower() == "aktif"
            and cctv.stream_url
        ),
    }
    return render_template("user/analyze.html", camera_idx=cctv.id, cctv_info=cctv_info)


@user_bp.route("/analyze_stream/<int:cctv_id>")
def analyze_stream(cctv_id: int):
    cctv = CCTV.query.get_or_404(cctv_id)
    if (
        not (
            cctv.status.lower() == "aktif"
            and cctv.stream_url
        )
        or not generate_frames
    ):
        return "Stream tidak mendukung deteksi.", 400
    return Response(
        generate_frames(cctv.stream_url, cctv.lokasi, detection_mode="enhanced"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@user_bp.route("/video_feed/<int:cctv_id>")
def video_feed(cctv_id: int):
    cctv = CCTV.query.get_or_404(cctv_id)
    if not cctv.stream_url or not generate_frames:
        return "URL Stream tidak valid.", 400
    return Response(
        generate_frames(cctv.stream_url, cctv.lokasi),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@user_bp.route("/video_feed_preview/<int:cctv_id>")
def video_feed_preview(cctv_id: int):
    cctv = CCTV.query.get_or_404(cctv_id)
    if not cctv.stream_url or not generate_frames_preview_only:
        return "URL Stream tidak tersedia.", 404
    return Response(
        generate_frames_preview_only(cctv.stream_url),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@user_bp.route("/api/scan/<int:cctv_id>/start")
def start_scan_api(cctv_id: int):
    cctv = CCTV.query.get_or_404(cctv_id)
    if not (
        cctv.status.lower() == "aktif"
        and cctv.stream_url
    ):
        return (
            jsonify({"error": "CCTV tidak memiliki stream yang valid untuk deteksi"}),
            400,
        )
    return jsonify(
        {
            "status": "success",
            "message": f"Deteksi dimulai untuk {cctv.lokasi}",
            "camera_idx": cctv.id,
        }
    )


@user_bp.route("/api/scan/<int:cctv_id>/stop")
def stop_scan_api(cctv_id: int):
    cctv = CCTV.query.get_or_404(cctv_id)
    return jsonify(
        {
            "status": "success",
            "message": "Deteksi dihentikan",
            "camera_idx": cctv.id,
            "location": cctv.lokasi,
        }
    )


@user_bp.route("/api/system/status")
def get_system_status():
    total = CCTV.query.count()
    active = CCTV.query.filter(CCTV.status.ilike("aktif")).count()
    capable = CCTV.query.filter(
        CCTV.status.ilike("aktif"),
        CCTV.stream_url.isnot(None),
    ).count()
    return jsonify(
        {
            "system_status": "operational",
            "total_cameras": total,
            "active_cameras": active,
            "detection_capable_cameras": capable,
        }
    )


@user_bp.route("/api/detection/supported_objects")
def get_supported_objects():
    return jsonify(
        {
            "supported_objects": [
                {"id": "person", "name": "Orang"},
                {"id": "car", "name": "Mobil"},
                {"id": "motorcycle", "name": "Motor"},
                {"id": "bus", "name": "Bus"},
            ]
        }
    )
