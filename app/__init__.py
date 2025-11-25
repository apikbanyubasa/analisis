from flask import Flask, redirect, url_for
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from flask_migrate import Migrate
from flask_socketio import SocketIO
from dotenv import load_dotenv  # 💥 BARU
import os  # 💥 BARU

# --- MUAT VARIABEL LINGKUNGAN DI AWAL MODUL ---
# Ini mencegah masalah di Flask CLI di mana config.py dibaca sebelum run.py memuat .env
load_dotenv(override=True)
# ----------------------------------------------

from config import Config  # Config diimpor SETELAH load_dotenv

# --- 1. INISIALISASI EKSTENSI (GLOBAL) ---
db = SQLAlchemy()
mail = Mail()
login_manager = LoginManager()
login_manager.login_view = "admin.login"
login_manager.login_message = "Silakan login untuk mengakses halaman ini."
login_manager.login_message_category = "info"

# Deklarasi global untuk SocketIO dan Migrate
socketio = SocketIO(cors_allowed_origins="*")
migrate = Migrate()

# JANGAN impor model 'User' di sini.


def create_app():
    """
    Application Factory Pattern: Membuat dan mengkonfigurasi instance aplikasi Flask.
    """
    app = Flask(__name__, static_folder="static")

    # --- 2. KONFIGURASI APLIKASI ---
    app.config.from_object(Config)

    # --- 3. HUBUNGKAN EKSTENSI DENGAN APLIKASI ---
    # Hubungkan ekstensi yang sudah dibuat dengan instance aplikasi.
    db.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)

    # HUBUNGKAN SOCKETIO KE APLIKASI (BARIS KRITIS UNTUK RUN.PY)
    socketio.init_app(app)

    # HUBUNGKAN FLASK-MIGRATE DENGAN APLIKASI DAN DB
    migrate.init_app(app, db)

    # --- PINDAHKAN IMPOR 'User' KE SINI ---
    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Gunakan app_context untuk mendaftarkan blueprint dan membuat tabel
    with app.app_context():
        # --- 4. DAFTARKAN BLUEPRINT ---
        from .user.routes import user_bp
        from .admin.routes import admin_bp
        from .seed import seed_bp  # <-- BARU: Impor blueprint seeder

        app.register_blueprint(user_bp, url_prefix="/user")
        app.register_blueprint(admin_bp, url_prefix="/admin")
        app.register_blueprint(seed_bp)  # <-- BARU: Daftarkan seeder command

        # Catatan: db.create_all() sebaiknya dihindari saat menggunakan Flask-Migrate.

    # --- 6. DAFTARKAN RUTE LEVEL APLIKASI ---
    @app.route("/")
    def home():
        # Mengarahkan halaman utama ke dashboard user
        return redirect(url_for("user.kepadatan"))

    return app
