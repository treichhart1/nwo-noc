"""Private immutable content-addressed files. No public paths, HTTP fetches or shells."""

import base64, hashlib, os, re, socket, struct, time
from io import BytesIO
from pathlib import Path
from app.core.config import settings
from app.equipment.service import verify_file


def root():
    p = Path(settings.maintenance_document_root)
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise ValueError("Absolute non-symlink document store required")
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    if p.stat().st_uid != os.getuid() or p.stat().st_mode & 0o077:
        raise ValueError(
            "Document store must be owned by runtime user with0700 permissions"
        )
    return p


def path(key):
    if not re.fullmatch("[0-9a-f]{64}", key):
        raise ValueError("Invalid storage key")
    p = root() / key[:2]
    if p.is_symlink():
        raise ValueError("Unsafe storage shard")
    return p / key


def put(raw):
    key = hashlib.sha256(raw).hexdigest()
    p = path(key)
    p.parent.mkdir(mode=0o700, exist_ok=True)
    tmp = p.parent / (".upload-" + __import__("uuid").uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
            os.fchmod(f.fileno(), 0o400)
        try:
            os.link(tmp, p)
        except FileExistsError:
            if read(key) != raw:
                raise ValueError("Existing document integrity failure")
        directory = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        tmp.unlink(missing_ok=True)
    return key


def read(key):
    p = path(key)
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        if not __import__("stat").S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError("Non-regular document file")
        raw = f.read(settings.maintenance_document_max_bytes + 1)
    if (
        len(raw) > settings.maintenance_document_max_bytes
        or hashlib.sha256(raw).hexdigest() != key
    ):
        raise ValueError("Document checksum mismatch or configured limit exceeded")
    return raw


def validate(b):
    try:
        raw = base64.b64decode(b.contents_base64, validate=True)
    except Exception:
        raise ValueError("Invalid document encoding") from None
    if not raw or len(raw) > settings.maintenance_document_max_bytes:
        raise ValueError("Document exceeds configured size limit")
    if b.media_type != "application/pdf":
        # Existing image/text validator is intentionally bounded at512KiB.
        verify_file(b)
    elif (
        b.filename.lower().endswith(".pdf")
        and raw.startswith(b"%PDF-")
        and b"%%EOF" in raw[-1024:]
    ):
        from pypdf import PdfReader

        try:
            reader = PdfReader(BytesIO(raw), strict=True)
            if reader.is_encrypted or len(reader.pages) > 500:
                raise ValueError("Encrypted or oversized PDF")
            # Reject action/embedded-content structures anywhere, including compressed objects.
            forbidden = {
                "/JavaScript",
                "/JS",
                "/OpenAction",
                "/AA",
                "/Launch",
                "/EmbeddedFiles",
                "/EmbeddedFile",
                "/RichMedia",
                "/XFA",
                "/AcroForm",
                "/SubmitForm",
                "/URI",
            }
            if (
                len(reader.xref) > 1000
                or sum(len(x) for x in reader.xref.values()) > 20000
            ):
                raise ValueError("PDF object limit")
            from pypdf.generic import IndirectObject, DictionaryObject, ArrayObject

            visited = set()
            budget = [0]

            def walk(obj, depth=0):
                budget[0] += 1
                if depth > 60 or budget[0] > 50000:
                    raise ValueError("PDF complexity limit")
                if isinstance(obj, IndirectObject):
                    ref = (obj.idnum, obj.generation)
                    if ref in visited:
                        return
                    visited.add(ref)
                    return walk(obj.get_object(), depth + 1)
                if isinstance(obj, DictionaryObject):
                    if forbidden.intersection(obj.keys()) or str(
                        obj.get("/Type", "")
                    ) in {"/Filespec", "/EmbeddedFile"}:
                        raise ValueError("Active/embedded PDF content forbidden")
                    for v in obj.values():
                        walk(v, depth + 1)
                elif isinstance(obj, ArrayObject):
                    for v in obj:
                        walk(v, depth + 1)

            walk(reader.trailer)
            for generation, entries in reader.xref.items():
                for id in entries:
                    if id:
                        walk(IndirectObject(id, generation, reader))
        except Exception:
            raise ValueError(
                "Unsupported or malformed PDF; plain static unencrypted reports only"
            ) from None
    else:
        raise ValueError("PDF content/type mismatch")
    return raw


def scan(raw):
    mode = settings.maintenance_document_scanner
    if mode == "disabled":
        return "quarantined", "disabled"
    if mode == "synthetic":
        if not (
            settings.offline_mode
            and settings.app_env == "staging"
            and settings.synthetic_monitoring_enabled
        ):
            raise ValueError("Synthetic scanner unavailable")
        return ("infected" if b"EICAR" in raw else "clean"), "synthetic-fixture"
    try:
        if not Path(settings.maintenance_clamav_socket).is_absolute():
            raise ValueError("Absolute scanner socket required")
        deadline = time.monotonic() + 5
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect(settings.maintenance_clamav_socket)
            sock.sendall(b"zINSTREAM\0")
            for i in range(0, len(raw), 65536):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Scan deadline")
                sock.settimeout(min(3, remaining))
                chunk = raw[i : i + 65536]
                sock.sendall(struct.pack("!I", len(chunk)) + chunk)
            sock.sendall(struct.pack("!I", 0))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Scan deadline")
            sock.settimeout(min(3, remaining))
            response = sock.recv(1024)
        if response.rstrip(b"\0\n").endswith(b": OK"):
            return "clean", "clamav"
        if b" FOUND" in response:
            return "infected", "clamav"
        return "error", "clamav"
    except (OSError, ValueError):
        return "error", "clamav"
