import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- KONFIGURASI EMAIL ---
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() in ["true", "on", "1"]
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "false").lower() in [
        "true",
        "on",
        "1",
    ]
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    # --- TAMBAHKAN BARIS INI ---
    MAIL_DEFAULT_SENDER = ("Admin DaashTics", os.environ.get("MAIL_DEFAULT_SENDER"))

    # --- KONFIGURASI RECAPTCHA ---
    RECAPTCHA_PUBLIC_KEY = os.environ.get("RECAPTCHA_PUBLIC_KEY")
    RECAPTCHA_PRIVATE_KEY = os.environ.get("RECAPTCHA_PRIVATE_KEY")

    # Pengecekan Kritis
    if not SECRET_KEY or not SQLALCHEMY_DATABASE_URI:
        raise ValueError(
            "SECRET_KEY dan DATABASE_URL tidak boleh kosong! Mohon set di file .env"
        )

    WA_API_URL = os.environ.get("WA_API_URL")
    WA_API_TOKEN = os.environ.get("WA_API_TOKEN")
