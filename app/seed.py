from flask import Blueprint
from werkzeug.security import generate_password_hash
from app.models import User
from app import db
import click
import sys
from sqlalchemy import select  # 💥 BARU: Impor select

# Buat Blueprint baru untuk Flask CLI commands
seed_bp = Blueprint("seed", __name__)


@seed_bp.cli.command("create-users")
@click.option("--username", default=None, help="Spesifikasi username untuk satu user.")
@click.option("--password", default="123456", help="Password default untuk user baru.")
def create_users_command(username, password):
    """Membuat akun user Admin dan Operator default."""
    try:
        # Jika username spesifik diberikan, buat hanya user itu
        if username:
            # 💥 Perbaikan Query: Menggunakan db.select().scalars().one_or_none()
            user = db.session.scalars(
                select(User).filter_by(username=username)
            ).one_or_none()

            if user:
                click.echo(f"⚠️ User '{username}' sudah ada. Melewati.")
                return

            new_user = User(
                username=username,
                email=f"{username}@example.com",
                # Asumsi: Jika user tunggal dibuat, atur role default ke operator
                role="operator",
            )
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            click.echo(
                click.style(
                    f"✅ User '{username}' (Operator) dibuat dengan password: {password}",
                    fg="green",
                )
            )
            return

        # Jika tidak ada username spesifik, buat user default (Admin & Operator)

        users_data = [
            {
                "username": "admin",
                "email": "apikby657@gmail.com",
                "role": "admin",
                "password": password,
            },
            {
                "username": "operator",
                "email": "apikbb123@gmail.com",
                "role": "operator",
                "password": password,
            },
        ]

        count = 0
        for data in users_data:
            # 💥 Perbaikan Query: Menggunakan db.select().scalars().one_or_none()
            user = db.session.scalars(
                select(User).filter_by(username=data["username"])
            ).one_or_none()

            if user:
                # Update password jika user sudah ada
                user.set_password(data["password"])
                db.session.add(user)
                click.echo(
                    f"⚠️ User '{data['username']}' sudah ada, password diupdate ke: {password}"
                )
            else:
                # Buat user baru
                new_user = User(
                    username=data["username"], email=data["email"], role=data["role"]
                )
                new_user.set_password(data["password"])
                db.session.add(new_user)
                click.echo(
                    click.style(
                        f"✅ User '{data['username']}' ({data['role'].title()}) dibuat dengan password: {password}",
                        fg="green",
                    )
                )
                count += 1

        db.session.commit()
        click.echo(
            click.style(f"\nBerhasil membuat/mengupdate {count} user.", fg="blue")
        )

    except Exception as e:
        db.session.rollback()
        # Menampilkan tipe error untuk debug yang lebih baik
        error_type = type(e).__name__
        click.echo(
            click.style(f"\n❌ Gagal membuat user ({error_type}): {e}", fg="red")
        )
        sys.exit(1)
