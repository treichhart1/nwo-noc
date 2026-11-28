"""Persistent email identity key, isolated from private JWT and provider-vault keys."""

import base64, hashlib, hmac
from pathlib import Path
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from app.core.config import settings
from app.notifications.secrets import cipher
from app.notifications.email_contracts import address


def contact_hash(value):
    path = settings.email_callback_key_file
    try:
        cipher(path)
        raw = base64.urlsafe_b64decode(Path(path).read_bytes().strip())
    except (OSError, ValueError):
        raise ValueError(
            "Email identity key unavailable; provision the approved private callback key file"
        ) from None
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"nwo-noc-email-contact-v1",
    ).derive(raw)
    return hmac.new(key, address(value).casefold().encode(), hashlib.sha256).hexdigest()
