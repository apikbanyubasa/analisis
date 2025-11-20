from functools import wraps
from flask import abort
from flask_login import current_user

def role_required(role):
    """
    Decorator untuk membatasi akses berdasarkan role.
    Hanya user dengan role yang sesuai yang bisa mengakses rute.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:

                return abort(401)
            if current_user.role != role:

                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def admin_required(f):
    """Shortcut decorator untuk role 'admin'."""
    return role_required('admin')(f)

def operator_or_admin_required(f):
    """
    Decorator untuk memperbolehkan akses bagi 'operator' atau 'admin'.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return abort(401)
        if current_user.role not in ['operator', 'admin']:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function
