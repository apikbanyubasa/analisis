from flask_wtf import FlaskForm, RecaptchaField  # <-- BAGIAN INI DIPERBAIKI
from wtforms import (
    StringField,
    PasswordField,
    SubmitField,
    BooleanField,
    SelectField,
    EmailField,
    FloatField,  # Diperlukan untuk CCTVForm
)
from wtforms.validators import (
    DataRequired,
    Length,
    Email,
    EqualTo,
    ValidationError,
    Optional,
)
from ..models import User
from flask_login import current_user


class LoginForm(FlaskForm):
    """Form untuk login user."""

    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Ingat Saya")
    recaptcha = RecaptchaField()  # <-- BARIS INI DITAMBAHKAN
    submit = SubmitField("Masuk")


class RegistrationForm(FlaskForm):
    """Form untuk admin mendaftarkan user baru."""

    username = StringField(
        "Username", validators=[DataRequired(), Length(min=4, max=25)]
    )
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Konfirmasi Password",
        validators=[
            DataRequired(),
            EqualTo("password", message="Password harus sama."),
        ],
    )
    submit = SubmitField("Daftarkan Pengguna")

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError(
                "Username tersebut sudah terdaftar. Silakan pilih nama lain."
            )

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError(
                "Email tersebut sudah terdaftar. Silakan gunakan email lain."
            )


class UpdateAccountForm(FlaskForm):
    """
    Form untuk memperbarui username dan email.
    Password di sini opsional, hanya diisi jika ingin mengubahnya.
    """

    username = StringField(
        "Username", validators=[DataRequired(), Length(min=4, max=25)]
    )
    email = EmailField("Email", validators=[DataRequired(), Email()])

    current_password = PasswordField("Password Saat Ini untuk Konfirmasi")

    new_password = PasswordField(
        "Password Baru (Opsional)",
        validators=[
            Optional(),
            Length(min=8, message="Password baru minimal 8 karakter."),
        ],
    )
    confirm_password = PasswordField(
        "Konfirmasi Password Baru",
        validators=[
            Optional(),
            EqualTo("new_password", message="Konfirmasi password tidak cocok."),
        ],
    )
    submit = SubmitField("Simpan Perubahan")

    # MENGHAPUS _init_ KUSTOM YANG BERMASALAH AGAR MENGGUNAKAN _init_ DEFAULT
    # def _init_(self, *args, **kwargs):
    #     super(UpdateAccountForm, self)._init_(*args, **kwargs)

    def validate_username(self, username):
        if username.data != current_user.username:
            user = User.query.filter_by(username=username.data).first()
            if user:
                raise ValidationError("Username tersebut sudah digunakan.")

    def validate_email(self, email):
        if email.data != current_user.email:
            user = User.query.filter_by(email=email.data).first()
            if user:
                raise ValidationError("Email tersebut sudah terdaftar.")


class ResetPasswordRequestForm(FlaskForm):
    """Form untuk meminta link reset password via email."""

    email = StringField("Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Kirim Link Reset Password")


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        "Password Baru",
        validators=[
            DataRequired(),
            Length(min=8, message="Password minimal 8 karakter."),
        ],
    )
    confirm_password = PasswordField(
        "Konfirmasi Password Baru",
        validators=[
            DataRequired(),
            EqualTo("password", message="Konfirmasi password tidak cocok."),
        ],
    )
    submit = SubmitField("Reset Password")


class VerifyOtpForm(FlaskForm):
    """Form sederhana untuk verifikasi OTP."""

    otp = StringField(
        "Kode OTP",
        validators=[DataRequired(), Length(min=6, max=6, message="OTP harus 6 digit.")],
    )
    submit = SubmitField("Verifikasi")


class CCTVForm(FlaskForm):
    """Form untuk menambah dan mengedit data CCTV."""

    lokasi = StringField("Lokasi", validators=[DataRequired()])
    status = SelectField(
        "Status",
        choices=[("Aktif", "Aktif"), ("Tidak Aktif", "Tidak Aktif")],
        validators=[DataRequired()],
    )
    latitude = FloatField("Latitude", validators=[Optional()])
    longitude = FloatField("Longitude", validators=[Optional()])
    video_url = StringField("Video URL (html)", validators=[Optional()])
    camera_type = StringField("Tipe Kamera", validators=[Optional()])
    stream_url = StringField("Stream URL (m3u8)", validators=[Optional()])

    tipe_lokasi = SelectField(
        "Tipe Lokasi",
        choices=[
            ("Area Publik", "Area Publik"),
            ("Pasar", "Pasar"),
            ("Taman", "Taman"),
            ("Tol", "Tol"),
        ],
        validators=[Optional()],
    )
    submit = SubmitField("Simpan")


# == FORM BARU UNTUK KONTAK DARURAT ==
class KontakForm(FlaskForm):
    """Form untuk menambah dan mengedit data Kontak."""

    instansi = StringField(
        "Nama Instansi", validators=[DataRequired(), Length(max=100)]
    )
    nomor_telp = StringField(
        "Nomor Telepon", validators=[DataRequired(), Length(max=20)]
    )
    submit = SubmitField("Simpan Kontak")


class EmptyForm(FlaskForm):
    """Form kosong untuk aksi yang hanya memerlukan tombol submit atau token CSRF."""

    pass
