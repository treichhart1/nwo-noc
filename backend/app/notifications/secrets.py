"""Provider credential vault, encrypted at rest; no delivery activation mechanism."""

import os, stat
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken


def cipher(path):
    p = Path(path)
    s = p.lstat()
    if (
        not stat.S_ISREG(s.st_mode)
        or s.st_uid != os.getuid()
        or s.st_mode & 0o077
        or s.st_size > 256
    ):
        raise ValueError("Private owned credential encryption key required")
    return Fernet(p.read_bytes().strip())


def seal(path, value):
    if not value or len(value) > 4096:
        raise ValueError("Bounded provider secret required")
    return cipher(path).encrypt(value.encode()).decode()


def unseal(path, value):
    try:
        return cipher(path).decrypt(value.encode()).decode()
    except (InvalidToken, UnicodeError):
        raise ValueError(
            "Credential unavailable; verify private encryption key"
        ) from None
