import os
import secrets
from datetime import datetime, timedelta
from flask import current_app
from . import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


class CCTV(db.Model):
    __tablename__ = "cctv"
    # PK sudah BIGINT
    id = db.Column(db.BigInteger, primary_key=True)
    lokasi = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), default="Aktif")
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    video_url = db.Column(db.String(500), nullable=True)
    camera_type = db.Column(db.String(100), nullable=True)
    stream_url = db.Column(db.String(500), nullable=True)
    type = db.Column(db.String(100), nullable=True)

    # === PERUBAHAN: Tambahkan cascade="all, delete-orphan" di sisi Parent ===
    counting_data_list = db.relationship("CountingData", backref="cctv", lazy=True, cascade="all, delete-orphan")
    parking_violations_list = db.relationship(
        "ParkingViolation", backref="cctv", lazy=True, cascade="all, delete-orphan"
    )
    crowd_detections_list = db.relationship("CrowdDetection", backref="cctv", lazy=True, cascade="all, delete-orphan")
    odol_detections_list = db.relationship("OdolDetection", backref="cctv", lazy=True, cascade="all, delete-orphan")
    # =======================================================================

    def __repr__(self):
        return f"<CCTV {self.lokasi}>"

class CountingData(db.Model):
    __tablename__ = "counting_data"
    id = db.Column(db.BigInteger, primary_key=True)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    # === PERUBAHAN: Tambahkan ondelete="CASCADE" di Foreign Key ===
    cctv_id = db.Column(
        db.BigInteger, db.ForeignKey("cctv.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # =============================================================

    counts_jauh_car = db.Column(db.Integer, default=0)
    counts_jauh_motorcycle = db.Column(db.Integer, default=0)
    counts_jauh_bus = db.Column(db.Integer, default=0)
    counts_jauh_truck = db.Column(db.Integer, default=0)

    counts_dekat_car = db.Column(db.Integer, default=0)
    counts_dekat_motorcycle = db.Column(db.Integer, default=0)
    counts_dekat_bus = db.Column(db.Integer, default=0)
    counts_dekat_truck = db.Column(db.Integer, default=0)

    grand_total = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f"<CountingData {self.cctv.lokasi if self.cctv else 'N/A'}@{self.timestamp}>"


class ParkingViolation(db.Model):
    __tablename__ = "parking_violations"
    id = db.Column(db.BigInteger, primary_key=True)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    # === PERUBAHAN: Tambahkan ondelete="CASCADE" di Foreign Key ===
    cctv_id = db.Column(
        db.BigInteger, db.ForeignKey("cctv.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # =============================================================

    vehicle_type = db.Column(db.String(50), nullable=False)
    parked_duration_sec = db.Column(db.Float, nullable=False)
    object_id = db.Column(db.Integer, nullable=False) 

    def __repr__(self):
        return f"<ParkingViolation {self.cctv.lokasi if self.cctv else 'N/A'} - {self.vehicle_type}>"


class CrowdDetection(db.Model):
    __tablename__ = "crowd_detections"
    id = db.Column(db.BigInteger, primary_key=True)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    # === PERUBAHAN: Tambahkan ondelete="CASCADE" di Foreign Key ===
    cctv_id = db.Column(
        db.BigInteger, db.ForeignKey("cctv.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # =============================================================

    crowd_size = db.Column(db.Integer, nullable=False)
    duration_sec = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<CrowdDetection {self.cctv.lokasi if self.cctv else 'N/A'} - {self.crowd_size} people>"


class OdolDetection(db.Model):
    __tablename__ = "odol_detections"
    id = db.Column(db.BigInteger, primary_key=True)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    # === PERUBAHAN: Tambahkan ondelete="CASCADE" di Foreign Key ===
    cctv_id = db.Column(
        db.BigInteger, db.ForeignKey("cctv.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # =============================================================

    vehicle_type = db.Column(
        db.String(50), nullable=False
    )
    aspect_ratio = db.Column(db.Float, nullable=False)
    area = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<OdolDetection {self.cctv.lokasi if self.cctv else 'N/A'} - {self.vehicle_type}>"

class BatasWilayah(db.Model):
    __tablename__ = "batas_wilayah"
    # === PERUBAHAN: id menjadi db.BigInteger ===
    id = db.Column(db.BigInteger, primary_key=True)
    # ==========================================
    nama = db.Column(db.String(255), nullable=False)
    # Jenis bisa 'Kabupaten' atau 'Kota'
    jenis = db.Column(db.String(50), nullable=False, index=True)
    geojson = db.Column(
        db.Text, nullable=True
    )  # Untuk menyimpan data koordinat poligon
    keterangan = db.Column(db.String(500), nullable=True)

    def __repr__(self):
        return f"<BatasWilayah {self.nama}>"


class Kontak(db.Model):
    __tablename__ = "kontak"
    # === PERUBAHAN: id menjadi db.BigInteger ===
    id = db.Column(db.BigInteger, primary_key=True)
    # ==========================================
    instansi = db.Column(db.String(100), nullable=False)
    nomor_telp = db.Column(db.String(20), nullable=False)
    # Icon bisa berupa class Font Awesome (e.g., 'fas fa-ambulance')

    def __repr__(self):
        return f"<Kontak {self.instansi}: {self.nomor_telp}>"


class Dispatch(db.Model):
    # === PERUBAHAN: id menjadi db.BigInteger ===
    id = db.Column(db.BigInteger, primary_key=True)
    # ==========================================

    # Foreign Key ke tabel Kontak (FK harus diubah ke BigInteger jika PK-nya BigInteger)
    kontak_id = db.Column(db.BigInteger, db.ForeignKey("kontak.id"), nullable=False)

    # Foreign Key ke tabel User (FK harus diubah ke BigInteger jika PK-nya BigInteger)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)

    # Kolom data
    tipe_dispatch = db.Column(db.String(50), nullable=False)
    instruksi = db.Column(db.Text, nullable=False)
    waktu_kirim = db.Column(db.DateTime, default=db.func.current_timestamp())
    status = db.Column(db.String(50), default="Terkirim")

    # Relasi
    kontak = db.relationship("Kontak", backref=db.backref("dispatches", lazy=True))
    operator = db.relationship(
        "User", backref=db.backref("sent_dispatches", lazy=True)
    )  # <--- BARU

    def __repr__(self):
        return f"Dispatch('{self.tipe_dispatch}', '{self.instruksi[:20]}...')"


class User(db.Model, UserMixin):
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}

    # === PERUBAHAN: id menjadi db.BigInteger ===
    id = db.Column(db.BigInteger, primary_key=True)
    # ==========================================
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="operator")

    reset_token = db.Column(db.String(100), unique=True, nullable=True)
    reset_token_expiration = db.Column(db.DateTime, nullable=True)
    otp_secret = db.Column(db.String(6), nullable=True)
    otp_expiration = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_reset_token(self, expires_sec=1800):
        self.reset_token = secrets.token_urlsafe(20)
        self.reset_token_expiration = datetime.utcnow() + timedelta(seconds=expires_sec)
        return self.reset_token

    @staticmethod
    def verify_reset_token(token):
        user = User.query.filter_by(reset_token=token).first()
        if user and user.reset_token_expiration > datetime.utcnow():
            return user
        return None

    def __repr__(self):
        return f"<User {self.username}>"

