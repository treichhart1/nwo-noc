import base64, hashlib, hmac, json
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings
from app.notifications.secrets import cipher


def master():
    p = Path(settings.signup_pii_key_file)
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise ValueError("Absolute non-symlink application key required")
    return cipher(settings.signup_pii_key_file)


def digest(domain, value):
    master()
    key = base64.urlsafe_b64decode(
        Path(settings.signup_pii_key_file).read_bytes().strip()
    )
    return hmac.new(key, (domain + "\0" + value).encode(), hashlib.sha256).hexdigest()


def data_cipher(a):
    if not a.wrapped_key:
        raise ValueError("Applicant data has been redacted")
    try:
        return Fernet(master().decrypt(a.wrapped_key.encode()))
    except InvalidToken:
        raise ValueError("Applicant key unavailable") from None


def seal(a, value):
    return (
        data_cipher(a)
        .encrypt(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        .decode()
    )


def unseal(a, value):
    if not a.wrapped_key or not value:
        return {"redacted": True}
    try:
        return json.loads(data_cipher(a).decrypt(value.encode()))
    except (InvalidToken, ValueError, UnicodeError):
        raise ValueError("Applicant data unavailable") from None


def initialize(a, pii):
    key = Fernet.generate_key()
    a.wrapped_key = master().encrypt(key).decode()
    a.pii_ciphertext = seal(a, pii)
