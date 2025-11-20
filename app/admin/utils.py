from flask_mail import Message
from app import mail
from flask import url_for, render_template


def send_password_reset_email(user):
    token = user.get_reset_token()
    # Menggunakan render_template untuk membuat isi email dari file HTML
    html_body = render_template(
        "auth/email/reset_password.html", user=user, token=token
    )
    msg = Message(
        "Permintaan Reset Password - DaashTics",
        sender=mail.default_sender,
        recipients=[user.email],
        html=html_body,
    )  # Mengirim email dalam format HTML
    mail.send(msg)


def send_otp_email(user, otp_code):
    """
    Fungsi baru untuk mengirim email berisi kode OTP.
    """
    html_body = render_template(
        "auth/email/send_otp.html", user=user, otp_code=otp_code
    )
    msg = Message(
        "Kode Verifikasi Perubahan Password - DaashTics",
        sender=mail.default_sender,
        recipients=[user.email],
        html=html_body,
    )
    mail.send(msg)
