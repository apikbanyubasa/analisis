import os
import secrets
import requests
import pandas as pd
import io
import json
import sqlite3
from collections import defaultdict, deque
import math
from datetime import datetime, timedelta
from analyzer import generate_frames, LATEST_DETECTION_STATS, LOCATION_TRACKERS
from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    request,
    flash,
    send_file,
    session,
    jsonify,
    Response,
    current_app,
)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from .. import db
from ..models import (
    User,
    CCTV,
    BatasWilayah,
    Kontak,
    Dispatch,
)
from .forms import (
    LoginForm,
    RegistrationForm,
    UpdateAccountForm,
    ResetPasswordRequestForm,
    ResetPasswordForm,
    VerifyOtpForm,
    CCTVForm,
    KontakForm,
    EmptyForm,
)
from .decorators import admin_required, operator_or_admin_required
from .utils import send_password_reset_email, send_otp_email
from shapely.wkt import loads as wkt_loads
from shapely.geometry import mapping
from workers_manager import reload_workers_thread
from flask import current_app

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

admin_bp = Blueprint("admin", __name__, template_folder="templates")


def load_cctv_from_csv():
    """Muat data CCTV dari file CSV"""
    csv_path = os.path.join(PROJECT_ROOT, "data", "cctv_bogor_clean.csv")
    print("========================================")
    print(f"DEBUG: Mencari CSV di path: {csv_path}")
    if not os.path.exists(csv_path):
        print("⚠️ File CSV tidak ditemukan!")
        return []

    df = pd.read_csv(csv_path)
    cameras = []
    for idx, row in df.iterrows():
        # Bersihkan semua nilai dari spasi ekstra
        status = str(row.get("STATUS", "")).replace("\u00a0", "").strip().lower()
        stream_url = str(row.get("STREAM_URL", "")).replace("\u00a0", "").strip()
        lokasi = str(row.get("LOKASI", f"Kamera {idx}")).replace("\u00a0", "").strip()

        if status == "aktif" and stream_url.endswith(".m3u8"):
            cameras.append(
                {
                    "index": idx,
                    "name": f"Kamera {str(idx).zfill(2)}",
                    "location": lokasi,
                    "status": "active",
                    "has_stream": True,
                    "stream_url": stream_url,
                }
            )
    return cameras


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.cctv"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            next_page = request.args.get("next")
            flash("Login berhasil!", "success")
            return (
                redirect(next_page) if next_page else redirect(url_for("admin.deteksi"))
            )
        else:
            flash("Login gagal. Periksa kembali username dan password Anda.", "danger")
    return render_template("admin/login.html", title="Login", form=form)


@admin_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Anda telah berhasil logout.", "success")
    return redirect(url_for("admin.login"))


@admin_bp.route("/users")
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.username).all()
    return render_template("admin/users.html", users=all_users)


@admin_bp.route("/users/add", methods=["GET", "POST"])
@login_required
@admin_required
def add_user():
    form = RegistrationForm()
    if form.validate_on_submit():
        new_user = User(
            username=form.username.data, email=form.email.data, role="operator"
        )
        new_user.set_password(form.password.data)
        db.session.add(new_user)
        db.session.commit()
        flash(f"Akun operator untuk {form.username.data} berhasil dibuat!", "success")
        return redirect(url_for("admin.users"))
    return render_template(
        "admin/register.html", title="Tambah Pengguna Baru", form=form
    )


@admin_bp.route("/users/delete/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    user_to_delete = User.query.get_or_404(user_id)
    if user_to_delete.id == current_user.id:
        flash("Anda tidak dapat menghapus akun Anda sendiri.", "danger")
        return redirect(url_for("admin.users"))
    db.session.delete(user_to_delete)
    db.session.commit()
    flash("User berhasil dihapus.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/")
@admin_bp.route("/deteksi")
@login_required
def deteksi():
    # Gunakan query database untuk mendapatkan semua CCTV
    all_cctv = CCTV.query.order_by(CCTV.lokasi).all()

    # Cari kamera default (featured_cctv) dari database
    featured_cctv = CCTV.query.filter(
        # Cari yang statusnya 'Aktif' (pastikan kapitalisasi sesuai data DB)
        CCTV.status.ilike("aktif"),
        CCTV.stream_url.isnot(None),
    ).first()

    # Siapkan data untuk JavaScript (sekarang menggunakan ID sebagai index)
    cctv_data_for_js = [
        {
            "id": c.id,  # <-- Ganti 'index' menjadi 'id'
            "lokasi": c.lokasi,
            "status": c.status,
            "type": c.type,
            "camera_type": c.camera_type,
            "latitude": c.latitude,
            "longitude": c.longitude,
            "video_url": c.video_url,
            "stream_url": c.stream_url,
        }
        for c in all_cctv
    ]

    return render_template(
        "admin/deteksi.html",
        # featured_cctv sekarang adalah objek CCTV dari DB
        default_camera=featured_cctv,
        cctv_data=cctv_data_for_js,  # Data ini akan digunakan oleh JavaScript
    )


@admin_bp.route("/cctv")
@login_required
def cctv():
    all_cctv = CCTV.query.order_by(CCTV.lokasi).all()
    form = EmptyForm()
    total_cctv = len(all_cctv)
    aktif_cctv = CCTV.query.filter_by(status="Aktif").count()
    nonaktif_cctv = total_cctv - aktif_cctv
    summary = {"total": total_cctv, "aktif": aktif_cctv, "nonaktif": nonaktif_cctv}
    return render_template("admin/cctv.html", data=all_cctv, form=form, summary=summary)


@admin_bp.route("/chat_center")
@login_required
def chat_center():
    # Ambil semua kontak (masih dibutuhkan untuk Konsol Dispatcher)
    all_kontak = Kontak.query.order_by(Kontak.instansi).all()

    # == LOGIKA BARU UNTUK RIWAYAT CHAT ==

    # 1. Ambil semua dispatch yang pernah terjadi, diurutkan dari terbaru
    all_dispatches = (
        db.session.query(Dispatch, Kontak)
        .join(Kontak, Dispatch.kontak_id == Kontak.id)
        .order_by(Dispatch.waktu_kirim.desc())
        .all()
    )

    # 2. Buat daftar unik kontak yang pernah di-dispatch (untuk Panel Kiri)
    # Gunakan dictionary untuk menyimpan kontak unik dan dispatch terakhirnya
    # Key: Kontak.id, Value: (Kontak_obj, Dispatch_terakhir_obj)
    recent_contacts_map = {}
    for dispatch, kontak in all_dispatches:
        if kontak.id not in recent_contacts_map:
            # Dispatch yang pertama kali dilihat saat loop dari terbaru adalah dispatch terakhir
            recent_contacts_map[kontak.id] = {
                "kontak": kontak,
                "last_dispatch": dispatch,
            }

    # Ubah map menjadi list untuk diteruskan ke template, diurutkan berdasarkan waktu dispatch terbaru
    recent_contacts_list = list(recent_contacts_map.values())

    # Urutkan berdasarkan waktu kirim dispatch terakhir (sebenarnya sudah terurut dari all_dispatches)
    # Tapi ini memastikan urutan jika ada perubahan logika di masa depan
    recent_contacts_list.sort(
        key=lambda x: x["last_dispatch"].waktu_kirim, reverse=True
    )

    return render_template(
        "admin/chat_center.html",
        all_kontak=all_kontak,  # Untuk Konsol Dispatcher (Panel Kanan)
        recent_contacts=recent_contacts_list,  # Data untuk Panel Kiri
        all_dispatches=all_dispatches,  # Semua riwayat dispatch untuk Chat Area (akan di filter di JS)
    )


@admin_bp.route("/api/dispatch", methods=["POST"])
@login_required
def send_dispatch():
    data = request.get_json()

    if not data:
        return jsonify({"success": False, "message": "Data JSON tidak ditemukan"}), 400

    kontak_id = data.get("kontak_id")
    tipe_dispatch = data.get("tipe_kejadian")
    instruksi = data.get("instruksi")

    if not all([kontak_id, tipe_dispatch, instruksi]):
        return jsonify({"success": False, "message": "Data tidak lengkap"}), 400

    try:
        kontak_instansi = Kontak.query.get(kontak_id)
        if not kontak_instansi:
            return jsonify({"success": False, "message": "ID Kontak tidak valid"}), 404

        # 1. Buat objek Dispatch baru
        new_dispatch = Dispatch(
            kontak_id=kontak_id,
            operator_id=current_user.id,
            tipe_dispatch=tipe_dispatch,
            instruksi=instruksi,
        )

        # 2. Simpan ke database
        db.session.add(new_dispatch)
        db.session.commit()

        # 3. KIRIM PESAN WHATSAPP (BARU)
        message_data = {
            "tipe_kejadian": tipe_dispatch,
            "instruksi": instruksi,
            "instansi_nama": kontak_instansi.instansi,
        }
        wa_success, wa_message = send_whatsapp_message(
            kontak_instansi.nomor_telp, message_data
        )

        # 4. Kembalikan respons sukses, sertakan status WA
        if wa_success:
            final_message = (
                f"Dispatch ke {kontak_instansi.instansi} berhasil dicatat. {wa_message}"
            )
        else:
            # Jika gagal kirim WA, tetap catat dispatch ke DB dan berikan peringatan
            final_message = f"Dispatch ke {kontak_instansi.instansi} berhasil dicatat, TAPI GAGAL KIRIM WA: {wa_message}"

        return (
            jsonify(
                {
                    "success": True,
                    "message": final_message,
                    "nomor_telp": kontak_instansi.nomor_telp,
                }
            ),
            201,
        )

    except Exception as e:
        db.session.rollback()
        return (
            jsonify(
                {"success": False, "message": f"Terjadi kesalahan server: {str(e)}"}
            ),
            500,
        )


def send_whatsapp_message(target_number, message_text):
    """Mengirim pesan melalui WhatsApp API (misalnya FonNte)."""
    api_url = current_app.config.get("WA_API_URL")
    api_token = current_app.config.get("WA_API_TOKEN")

    if not api_url or not api_token:
        # Jika konfigurasi API belum diset
        return False, "Konfigurasi WhatsApp API belum diset di server."

    # FonNte (sesuaikan dengan API yang Anda gunakan):
    # Nomor harus diawali dengan kode negara (misal 62) tanpa '+'
    # Anggap nomor_telp di DB sudah dalam format yang benar (misal 0812...)
    # Konversi dari 08... atau +62... menjadi 62...
    if target_number.startswith("+62"):
        formatted_number = target_number.replace("+", "")
    elif target_number.startswith("0"):
        formatted_number = "62" + target_number[1:]
    else:
        # Jika sudah format 62...
        formatted_number = target_number

    payload = {
        "target": formatted_number,
        "message": f"DISPATCH BARU - Tipe: {message_text['tipe_kejadian']} - Instruksi: {message_text['instruksi']} - Instansi: {message_text['instansi_nama']}",
    }
    headers = {
        "Authorization": api_token,
        "Content-Type": "application/x-www-form-urlencoded",  # Sesuaikan dengan kebutuhan API
    }

    try:
        # Kirim request POST ke API
        response = requests.post(api_url, data=payload, headers=headers)
        response.raise_for_status()  # Raise HTTPError untuk status 4xx/5xx

        # Sesuaikan dengan respons API Anda (misal FonNte mengembalikan JSON)
        result = response.json()

        # FonNte: Cek status pengiriman (ganti sesuai respon API Anda)
        if result.get("status") and (
            result["status"] == "success" or result["status"] == True
        ):
            return True, f"Pesan WhatsApp berhasil dikirim ke {formatted_number}."
        else:
            return (
                False,
                f"Gagal kirim WA (API Error): {result.get('reason', 'Tidak ada alasan spesifik.')}",
            )

    except requests.exceptions.RequestException as e:
        return False, f"Kesalahan koneksi API WhatsApp: {e}"
    except Exception as e:
        return False, f"Kesalahan tidak terduga saat memproses WA: {e}"


@admin_bp.route("/nomor")
@login_required
def nomor():
    # Ambil semua data kontak dari database, diurutkan berdasarkan nama instansi
    all_kontak = Kontak.query.order_by(Kontak.instansi).all()
    form = EmptyForm()  # Digunakan untuk tombol delete
    return render_template("admin/nomor.html", data_kontak=all_kontak, form=form)


@admin_bp.route("/nomor/add", methods=["GET", "POST"])
@login_required
@operator_or_admin_required
def add_kontak():
    form = KontakForm()

    if form.validate_on_submit():
        try:
            new_kontak = Kontak(
                instansi=form.instansi.data,
                nomor_telp=form.nomor_telp.data,
            )
            db.session.add(new_kontak)
            db.session.commit()
            flash("Data kontak berhasil ditambahkan!", "success")
            return redirect(url_for("admin.nomor"))

        except Exception as e:
            db.session.rollback()
            # CETAK ERROR LENGKAP KE KONSEL SERVER
            print(f"DATABASE SAVE ERROR (Kontak): {e}")
            flash("Gagal menyimpan data. Cek error di konsol server.", "danger")
            return redirect(url_for("admin.nomor"))

    # Jika form tidak divalidasi (GET request atau validation error), render template
    return render_template(
        "admin/kontak_form.html",
        form=form,
        title="Tambah Kontak",
        page_title="Tambah Kontak",
        page_subtitle="Tambahkan data kontak darurat baru",
    )


@admin_bp.route("/nomor/edit/<int:kontak_id>", methods=["GET", "POST"])
@login_required
@operator_or_admin_required
def edit_kontak(kontak_id):
    kontak = Kontak.query.get_or_404(kontak_id)
    form = KontakForm(obj=kontak)

    if form.validate_on_submit():
        kontak.instansi = form.instansi.data
        kontak.nomor_telp = form.nomor_telp.data
        db.session.commit()
        flash("Data kontak berhasil diperbarui!", "success")
        return redirect(url_for("admin.nomor"))

    elif request.method == "GET":
        # Isi form dengan data yang sudah ada (redundant because of obj=kontak, but safer)
        form.instansi.data = kontak.instansi
        form.nomor_telp.data = kontak.nomor_telp

    return render_template(
        "admin/kontak_form.html",
        form=form,
        title="Edit Kontak",
        page_title="Edit Kontak",
        page_subtitle=f"Perbarui data {kontak.instansi}",
    )


@admin_bp.route("/nomor/delete/<int:kontak_id>", methods=["POST"])
@login_required
@operator_or_admin_required
def delete_kontak(kontak_id):
    form = EmptyForm()
    if form.validate_on_submit():
        kontak_to_delete = Kontak.query.get_or_404(kontak_id)
        db.session.delete(kontak_to_delete)
        db.session.commit()
        flash(f"Data kontak '{kontak_to_delete.instansi}' telah dihapus.", "success")
    else:
        # Fallback if CSRF fails or method is wrong
        flash("Permintaan penghapusan tidak valid.", "danger")

    return redirect(url_for("admin.nomor"))


@admin_bp.route("/history")
@login_required
def history():
    # ... (Kode yang sudah ada untuk mengambil all_dispatches)
    all_dispatches = (
        db.session.query(Dispatch, Kontak, User)
        .join(Kontak, Dispatch.kontak_id == Kontak.id)
        .join(User, Dispatch.operator_id == User.id)
        .order_by(Dispatch.waktu_kirim.desc())
        .all()
    )

    # Inisialisasi EmptyForm untuk digunakan sebagai CSRF token pada tombol hapus
    form = EmptyForm()

    return render_template(
        "admin/history.html", all_dispatches=all_dispatches, form=form
    )


@admin_bp.route("/history/delete/<int:dispatch_id>", methods=["POST"])
@login_required
@operator_or_admin_required  # Hanya operator atau admin yang boleh menghapus
def delete_dispatch(dispatch_id):
    form = EmptyForm()
    if form.validate_on_submit():
        dispatch_to_delete = Dispatch.query.get_or_404(dispatch_id)
        try:
            db.session.delete(dispatch_to_delete)
            db.session.commit()
            flash("Riwayat dispatch telah dihapus.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Gagal menghapus dispatch: {e}", "danger")
    else:
        # Fallback jika CSRF gagal atau method salah
        flash("Permintaan penghapusan tidak valid.", "danger")

    return redirect(url_for("admin.history"))


@admin_bp.route("/cctv/add", methods=["GET", "POST"])
@login_required
@operator_or_admin_required
def add_cctv():
    form = CCTVForm()
    if form.validate_on_submit():
        new_cctv = CCTV(
            lokasi=form.lokasi.data,
            status=form.status.data,
            latitude=form.latitude.data,
            longitude=form.longitude.data,
            video_url=form.video_url.data,
            camera_type=form.camera_type.data,
            stream_url=form.stream_url.data,
            type=form.tipe_lokasi.data,
        )
        db.session.add(new_cctv)
        db.session.commit()
        reload_workers_thread(current_app._get_current_object())
        flash("Data CCTV baru berhasil ditambahkan!", "success")
        return redirect(url_for("admin.cctv"))
    return render_template(
        "admin/cctv_form.html", action="add", form=form, title="Tambah CCTV"
    )


@admin_bp.route("/cctv/edit/<int:cctv_id>", methods=["GET", "POST"])
@login_required
@operator_or_admin_required
def edit_cctv(cctv_id):
    cctv_to_edit = CCTV.query.get_or_404(cctv_id)
    form = CCTVForm(obj=cctv_to_edit)
    if request.method == "GET":
        form.tipe_lokasi.data = cctv_to_edit.type
    if form.validate_on_submit():
        form.populate_obj(cctv_to_edit)
        cctv_to_edit.type = form.tipe_lokasi.data
        db.session.commit()
        flash("Data CCTV berhasil diperbarui!", "success")
        return redirect(url_for("admin.cctv"))
    return render_template(
        "admin/cctv_form.html",
        action="edit",
        form=form,
        title="Edit CCTV",
    )


@admin_bp.route("/cctv/delete/<int:cctv_id>", methods=["POST"])
@login_required
@operator_or_admin_required
def delete_cctv(cctv_id):
    cctv_to_delete = CCTV.query.get_or_404(cctv_id)
    location_name = cctv_to_delete.lokasi  # Simpan nama lokasi

    # 1. Hapus dari DB
    db.session.delete(cctv_to_delete)
    db.session.commit()

    # --- PENGHAPUSAN KRITIS (Hapus state lokal sebelum reload) ---
    if location_name in LATEST_DETECTION_STATS:
        del LATEST_DETECTION_STATS[location_name]
    if location_name in LOCATION_TRACKERS:
        del LOCATION_TRACKERS[location_name]
    # --- AKHIR PENGHAPUSAN KRITIS ---

    # 2. Memicu reload (Mematikan thread worker lama secara paksa)
    reload_workers_thread(current_app._get_current_object())

    flash("Data CCTV telah dihapus dan worker terkait dihentikan.", "success")
    return redirect(url_for("admin.cctv"))


@admin_bp.route("/cctv/delete_all", methods=["POST"])
@login_required
@admin_required
def delete_all_cctv():
    try:
        all_cctv_to_delete = CCTV.query.all()
        locations_to_cleanup = [cctv.lokasi for cctv in all_cctv_to_delete]

        # 1. KIRIM SINYAL STOP & TUNGGU (dipanggil di initialize_workers_and_server)
        reload_workers_thread(current_app._get_current_object())

        # 2. Hapus Global State secara paksa (Meskipun worker lama sedang mati)
        for location_name in locations_to_cleanup:
            if location_name in LATEST_DETECTION_STATS:
                del LATEST_DETECTION_STATS[location_name]
            if location_name in LOCATION_TRACKERS:
                del LOCATION_TRACKERS[location_name]

        # 3. Hapus dari database (Langkah terakhir)
        CCTV.query.delete()
        db.session.commit()

        flash(
            "Semua data CCTV telah berhasil dihapus dan worker dihentikan.", "success"
        )

    except Exception as e:
        db.session.rollback()
        flash(f"Terjadi kesalahan saat menghapus data: {e}", "danger")

    return redirect(url_for("admin.cctv"))


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    form = UpdateAccountForm()
    if form.validate_on_submit():
        is_profile_changed = (
            form.username.data != current_user.username
            or form.email.data != current_user.email
        )
        is_password_changed = form.new_password.data
        if not is_profile_changed and not is_password_changed:
            flash("Tidak ada perubahan yang disimpan.", "info")
            return redirect(url_for("admin.settings"))
        if not form.current_password.data:
            flash(
                "Silakan masukkan password Anda saat ini untuk konfirmasi perubahan.",
                "danger",
            )
            return redirect(url_for("admin.settings"))
        if not current_user.check_password(form.current_password.data):
            flash("Password saat ini salah.", "danger")
            return redirect(url_for("admin.settings"))
        session["pending_changes"] = {
            "username": form.username.data,
            "email": form.email.data,
            "new_password": form.new_password.data,
        }
        otp = secrets.token_hex(3).upper()
        current_user.otp_secret = otp
        current_user.otp_expiration = datetime.utcnow() + timedelta(minutes=10)
        db.session.commit()
        send_otp_email(current_user, otp)
        flash("Kode OTP telah dikirim ke email Anda untuk verifikasi.", "info")
        return redirect(url_for("admin.verify_changes"))
    elif request.method == "GET":
        form.username.data = current_user.username
        form.email.data = current_user.email
    return render_template("admin/settings.html", title="Pengaturan Akun", form=form)


@admin_bp.route("/settings/verify", methods=["GET", "POST"])
@login_required
def verify_changes():
    form = VerifyOtpForm()
    if form.validate_on_submit():
        if (
            current_user.otp_secret == form.otp.data.upper()
            and current_user.otp_expiration > datetime.utcnow()
        ):
            pending_changes = session.pop("pending_changes", {})
            current_user.username = pending_changes.get(
                "username", current_user.username
            )
            current_user.email = pending_changes.get("email", current_user.email)
            new_password = pending_changes.get("new_password")
            if new_password:
                current_user.set_password(new_password)
            current_user.otp_secret = None
            current_user.otp_expiration = None
            db.session.commit()
            flash("Perubahan berhasil diverifikasi dan disimpan.", "success")
            return redirect(url_for("admin.settings"))
        else:
            flash("Kode OTP salah atau telah kedaluwarsa.", "danger")
    return render_template(
        "auth/verify_otp.html", title="Verifikasi Perubahan", form=form
    )


@admin_bp.route("/reset_password", methods=["GET", "POST"])
def reset_request():
    if current_user.is_authenticated:
        return redirect(url_for("admin.cctv"))
    form = ResetPasswordRequestForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            send_password_reset_email(user)
        flash(
            "Jika email tersebut terdaftar, instruksi reset password telah dikirim.",
            "info",
        )
        return redirect(url_for("admin.login"))
    return render_template("auth/reset_request.html", title="Reset Password", form=form)


@admin_bp.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for("admin.cctv"))
    user = User.verify_reset_token(token)
    if user is None:
        flash("Token tidak valid atau telah kedaluwarsa.", "warning")
        return redirect(url_for("admin.reset_request"))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.reset_token = None
        user.reset_token_expiration = None
        db.session.commit()
        flash("Password Anda telah berhasil diubah! Silakan login.", "success")
        return redirect(url_for("admin.login"))
    return render_template("auth/reset_token.html", title="Reset Password", form=form)


@admin_bp.route("/upload-csv", methods=["POST"])
@login_required
@operator_or_admin_required
def upload_csv():
    if "csv_file" not in request.files:
        flash("Tidak ada file yang dipilih.", "danger")
        return redirect(url_for("admin.cctv"))
    file = request.files["csv_file"]
    if file.filename == "":
        flash("Tidak ada file yang dipilih.", "danger")
        return redirect(url_for("admin.cctv"))
    if file and file.filename.endswith(".csv"):
        try:
            csv_file = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            df = pd.read_csv(csv_file)
            df.columns = [col.lower() for col in df.columns]
            required_csv_columns = ["lokasi", "status"]
            if not all(col in df.columns for col in required_csv_columns):
                flash(
                    f'File CSV harus memiliki kolom: {", ".join(required_csv_columns)}.',
                    "danger",
                )
                return redirect(url_for("admin.cctv"))
            count_added = 0
            count_updated = 0
            for index, row in df.iterrows():
                cctv_entry = CCTV.query.filter(
                    db.func.lower(CCTV.lokasi) == row["lokasi"].lower()
                ).first()
                if not cctv_entry:
                    cctv_entry = CCTV(lokasi=row["lokasi"])
                    db.session.add(cctv_entry)
                    count_added += 1
                else:
                    count_updated += 1
                cctv_entry.status = row.get("status")
                cctv_entry.latitude = pd.to_numeric(
                    row.get("latitude"), errors="coerce"
                )
                cctv_entry.longitude = pd.to_numeric(
                    row.get("longitude"), errors="coerce"
                )
                cctv_entry.video_url = row.get("video_url")
                cctv_entry.camera_type = row.get("camera_type")
                cctv_entry.stream_url = row.get("stream_url")
                cctv_entry.type = row.get("type")
            db.session.commit()
            reload_workers_thread(current_app._get_current_object())
            flash(
                f"{count_added} data ditambahkan, {count_updated} data diperbarui dari CSV!",
                "success",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Terjadi error saat memproses file CSV: {e}", "danger")
    else:
        flash("Format file tidak valid. Harap unggah file .csv.", "danger")
    return redirect(url_for("admin.cctv"))


def get_cctv_dataframe():
    query = CCTV.query.all()
    data = [
        {
            "ID": c.id,
            "LOKASI": c.lokasi,
            "STATUS": c.status,
            "latitude": c.latitude,
            "longitude": c.longitude,
            "VIDEO_URL": c.video_url,
            "CAMERA_TYPE": c.camera_type,
            "STREAM_URL": c.stream_url,
            "TYPE": c.type,
        }
        for c in query
    ]
    return pd.DataFrame(data)


@admin_bp.route("/download-excel")
@login_required
def download_excel():
    df = get_cctv_dataframe()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="CCTV")
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="cctv_data.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@admin_bp.route("/download-csv")
@login_required
def download_csv():
    df = get_cctv_dataframe()
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        as_attachment=True,
        download_name="cctv_data.csv",
        mimetype="text/csv",
    )


# ===================================================================
# == RUTE BARU UNTUK MANAJEMEN BATAS & PETA
# ===================================================================


@admin_bp.route("/batas")
@login_required
def batas():
    """Menampilkan halaman manajemen data batas dan peta."""
    form = EmptyForm()
    try:
        data = {
            "batas_kab": BatasWilayah.query.filter_by(jenis="Kabupaten")
            .order_by(BatasWilayah.nama)
            .all(),
            "batas_kota": BatasWilayah.query.filter_by(jenis="Kota")
            .order_by(BatasWilayah.nama)
            .all(),
        }
    except Exception as e:
        flash(f"Gagal memuat data peta: {e}", "danger")
        data = {
            "batas_kab": [],
            "batas_kota": [],
        }
    return render_template("admin/batas.html", form=form, data=data)


@admin_bp.route("/batas/upload-csv/<tipe_data>", methods=["POST"])
@login_required
@admin_required
def upload_batas_csv(tipe_data):

    if "csv_file" not in request.files:
        flash("Tidak ada file yang dipilih.", "danger")
        return redirect(url_for("admin.batas"))

    file = request.files["csv_file"]
    if file.filename == "":
        flash("Tidak ada file yang dipilih.", "danger")
        return redirect(url_for("admin.batas"))

    if not file.filename.endswith(".csv"):
        flash("Format file tidak valid. Harap unggah file .csv.", "danger")
        return redirect(url_for("admin.batas"))

    # ===== KONFIGURASI YANG HANYA MENYENTUH BatasWilayah =====
    config = {
        "batas_kota": {
            "model": BatasWilayah,
            "required_cols": ["name_3", "geom"],
            "col_map": {"name_3": "nama", "geom": "geojson", "type_3": "keterangan"},
            "static_data": {"jenis": "Kota"},
        },
    }

    if tipe_data not in config:
        flash(
            f"Tipe data '{tipe_data}' tidak valid atau tidak didukung lagi.", "danger"
        )
        return redirect(url_for("admin.batas"))

    current_config = config[tipe_data]
    Model = current_config["model"]
    required_cols = current_config["required_cols"]
    col_map = current_config["col_map"]
    static_data = current_config["static_data"]

    try:
        csv_file = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        df = pd.read_csv(csv_file)
        # Penting: Mengubah semua nama kolom menjadi huruf kecil agar konsisten
        df.columns = [col.lower() for col in df.columns]

        if not all(col in df.columns for col in required_cols):
            flash(
                f"File CSV harus memiliki kolom: {', '.join(required_cols)}.", "danger"
            )
            return redirect(url_for("admin.batas"))

        for index, row in df.iterrows():
            data_to_insert = static_data.copy()

            # Memproses kolom yang didefinisikan di col_map
            for csv_col, model_attr in col_map.items():
                if csv_col in df.columns:
                    # ===== LOGIKA KONVERSI WKT KE GEOJSON =====
                    if model_attr == "geojson":
                        try:
                            wkt_string = str(row[csv_col])
                            shapely_geom = wkt_loads(wkt_string)
                            geojson_dict = mapping(shapely_geom)
                            # Simpan sebagai string JSON yang valid
                            data_to_insert[model_attr] = json.dumps(geojson_dict)
                        except Exception:
                            flash(
                                f"Baris {index + 2}: Format WKT di kolom '{csv_col}' tidak valid. Baris ini dilewati.",
                                "warning",
                            )
                            continue
                    else:
                        data_to_insert[model_attr] = row[csv_col]

            # Memproses kolom wajib yang mungkin tidak ada di col_map
            for col in required_cols:
                if col not in col_map and col in df.columns:
                    data_to_insert[col] = row[col]

            # Logika update atau buat baru
            # HANYA untuk BatasWilayah
            if (
                Model == BatasWilayah
                and "nama" in data_to_insert
                and "jenis" in data_to_insert
            ):
                entry = Model.query.filter_by(
                    nama=data_to_insert["nama"], jenis=data_to_insert["jenis"]
                ).first()
                if entry:  # Update jika sudah ada
                    if "geojson" in data_to_insert:
                        entry.geojson = data_to_insert["geojson"]
                else:  # Buat baru jika belum ada
                    entry = Model(**data_to_insert)
                    db.session.add(entry)
            else:  # Ini seharusnya tidak terjadi jika tipe_data sudah difilter
                new_entry = Model(**data_to_insert)
                db.session.add(new_entry)

        db.session.commit()
        flash(
            f"Data '{tipe_data.replace('_', ' ').title()}' berhasil diimpor.", "success"
        )

    except Exception as e:
        db.session.rollback()
        flash(f"Terjadi error saat memproses file: {e}", "danger")

    return redirect(url_for("admin.batas"))


@admin_bp.route("/batas/delete-all/<tipe_data>", methods=["POST"])
@login_required
@admin_required
def delete_all_batas(tipe_data):
    """Satu rute untuk menghapus semua data dari model tertentu.
    Hanya BatasWilayah yang didukung."""
    model_map = {
        "batas_kabupaten": BatasWilayah,
        "batas_kota": BatasWilayah,
    }

    Model = model_map.get(tipe_data)
    if not Model:
        flash("Tipe data tidak valid.", "danger")
        return redirect(url_for("admin.batas"))

    try:
        if tipe_data == "batas_kabupaten":
            Model.query.filter_by(jenis="Kabupaten").delete()
        elif tipe_data == "batas_kota":
            Model.query.filter_by(jenis="Kota").delete()
        else:
            # Menambahkan pesan error jika ada yang mencoba menghapus tipe data yang tidak ada
            flash(
                f"Penghapusan massal untuk tipe data '{tipe_data}' tidak didukung lagi.",
                "danger",
            )
            return redirect(url_for("admin.batas"))

        db.session.commit()
        flash(
            f"Semua data '{tipe_data.replace('_', ' ').title()}' telah dihapus.",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        flash(f"Gagal menghapus data: {e}", "danger")

    return redirect(url_for("admin.batas"))


# routes.py


@admin_bp.route("/api/crowd/cameras")
@login_required
def get_crowd_cameras():
    """Get list of all active cameras with stream capability dari DATABASE."""
    try:
        # 1. Ambil semua CCTV dari database
        all_cctv = CCTV.query.order_by(CCTV.lokasi).all()

        # 2. Siapkan data, filter hanya yang aktif dan punya stream
        cameras = []
        for c in all_cctv:
            # Pastikan hanya kamera aktif dan memiliki URL stream yang dimasukkan
            if c.status.lower() == "aktif" and c.stream_url:
                cameras.append(
                    {
                        "id": c.id,  # PENTING: Menggunakan ID DB
                        "name": f"Kamera {str(c.id).zfill(2)}",  # Contoh nama (Anda bisa pakai c.lokasi)
                        "lokasi": c.lokasi,  # PENTING: Menggunakan 'lokasi'
                        "type": c.type,
                        "status": c.status.lower(),
                        "stream_url": c.stream_url,
                    }
                )

        return jsonify({"total": len(cameras), "cameras": cameras})
    except Exception as e:
        print(f"Error in get_crowd_cameras: {e}")
        return jsonify({"error": str(e)}), 500


# Hapus atau abaikan fungsi load_cctv_from_csv() dari sini.


@admin_bp.route("/api/crowd/stats/<int:camera_id>")  # <-- Ganti ke camera_id
@login_required
def get_crowd_stats(camera_id: int):  # <-- Ganti ke camera_id
    # 1. Ambil kamera dari database menggunakan ID
    camera = get_camera_by_id(camera_id)

    if not camera:
        # Jika ID 250 tidak ditemukan di DB, kembalikan 404
        return jsonify({"error": "CCTV tidak ditemukan"}), 404

    # 2. Gunakan LOKASI dari objek DB sebagai kunci
    location_name = camera.lokasi  # <-- Gunakan .lokasi dari DB object

    if location_name in LATEST_DETECTION_STATS:
        stats = LATEST_DETECTION_STATS[location_name]
        total_counts = stats.get("total_counts", {})
        total_vehicles = (
            total_counts.get("car", 0)
            + total_counts.get("motorcycle", 0)
            + total_counts.get("bus", 0)
        )
        crowd_size = stats.get("crowd_size", 0)
        is_crowd = crowd_size > 0

        return jsonify(
            {
                "status": "active",
                "location": location_name,
                "people_count": total_counts.get("person", 0),
                "motion_events": stats.get("current_tracking", 0),
                "crowd_size": crowd_size,
                "total_vehicles": total_vehicles,
                "is_crowd_detected": is_crowd,
                "grand_total": stats.get("grand_total", 0),
                "timestamp": stats.get("timestamp", 0),
            }
        )
    else:
        return jsonify(
            {
                # Jika LOKASI ada di DB tapi tidak ada di stats (idle)
                "status": "idle",
                "location": location_name,
                "people_count": 0,
                "motion_events": 0,
                "crowd_size": 0,
                "total_vehicles": 0,
                "is_crowd_detected": False,
                "grand_total": 0,
                "timestamp": 0,
            }
        )


# Catatan: Pastikan Anda menghapus load_cctv_from_csv() sepenuhnya atau
# komentar DEBUG yang mencarinya. Jika fungsi tersebut masih dipanggil di
# tempat lain, Anda harus memperbaikinya juga.


@admin_bp.route("/api/parking_violations")
@login_required
def get_parking_violations():
    try:
        conn = sqlite3.connect("traffic_counting.db")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT timestamp, location, vehicle_type, parked_duration_sec, object_id
            FROM parking_violations
            WHERE datetime(timestamp) >= datetime('now', '-1 hour')
            ORDER BY timestamp DESC
            LIMIT 20
        """
        )
        rows = cursor.fetchall()
        conn.close()

        return jsonify(
            [
                {
                    "timestamp": r[0],
                    "location": r[1],
                    "vehicle_type": r[2],
                    "parked_duration_sec": r[3],
                    "object_id": r[4],
                }
                for r in rows
            ]
        )
    except Exception as e:
        print(f"Error parking violations: {e}")
        return jsonify([])


@admin_bp.route("/api/odol_detections")
@login_required
def get_odol_detections():
    try:
        conn = sqlite3.connect("traffic_counting.db")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT timestamp, location, vehicle_type, aspect_ratio, area
            FROM odol_detections
            WHERE datetime(timestamp) >= datetime('now', '-1 hour')
            ORDER BY timestamp DESC
            LIMIT 20
        """
        )
        rows = cursor.fetchall()
        conn.close()

        return jsonify(
            [
                {
                    "timestamp": r[0],
                    "location": r[1],
                    "vehicle_type": r[2],
                    "aspect_ratio": r[3],
                    "area": r[4],
                }
                for r in rows
            ]
        )
    except Exception as e:
        print(f"Error ODOL detections: {e}")
        return jsonify([])


def get_camera_by_id(camera_id):
    """Fungsi helper baru untuk mencari kamera berdasarkan ID database-nya."""
    # Gunakan ID untuk mencari objek CCTV langsung dari database
    return CCTV.query.get(camera_id)


# routes.py


@admin_bp.route("/crowd_stream/<int:camera_id>")  # <-- Ganti camera_idx ke camera_id
@login_required
def crowd_video_stream(camera_id: int):
    # Gunakan ID untuk mencari objek CCTV
    camera = get_camera_by_id(camera_id)
    if not camera or not camera.stream_url:
        return "CCTV tidak ditemukan atau stream tidak ada", 404

    return Response(
        generate_frames(
            camera.stream_url,
            camera.lokasi,
            detection_mode="simple",  # <-- Gunakan atribut model
        ),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# Lakukan hal yang sama untuk parking_video_stream dan plate_video_stream
@admin_bp.route("/parking_stream/<int:camera_id>")
@login_required
def parking_video_stream(camera_id: int):
    camera = get_camera_by_id(camera_id)
    if not camera or not camera.stream_url:
        return "CCTV tidak ditemukan atau stream tidak ada", 404

    return Response(
        generate_frames(camera.stream_url, camera.lokasi, detection_mode="parking"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@admin_bp.route("/plate_stream/<int:camera_id>")
@login_required
def plate_video_stream(camera_id: int):
    camera = get_camera_by_id(camera_id)
    if not camera or not camera.stream_url:
        return "CCTV tidak ditemukan atau stream tidak ada", 404

    return Response(
        generate_frames(camera.stream_url, camera.lokasi, detection_mode="plate"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
