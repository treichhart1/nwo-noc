"""Callback identity key is separate from JWT and outbound API credential keys."""

import base64
import hashlib
import hmac
from pathlib import Path
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from app.core.config import settings
from app.notifications.secrets import cipher


def contact_hash(phone):
    path = settings.sms_callback_key_file
    try:
        cipher(path)  # Reuse strict private-owned Fernet key validation.
        raw = base64.urlsafe_b64decode(Path(path).read_bytes().strip())
    except (OSError, ValueError):
        raise ValueError(
            "SMS callback identity key unavailable; provision the approved private key file"
        ) from None
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"nwo-noc-sms-contact-v1"
    ).derive(raw)
    return hmac.new(key, phone.encode(), hashlib.sha256).hexdigest()
