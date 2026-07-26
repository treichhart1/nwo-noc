from datetime import datetime, timezone
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.entities import Device, Integration

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None

def get_integration(db: Session):
    row = db.scalar(select(Integration).where(Integration.name == "uisp"))
    if not row:
        row = Integration(name="uisp")
        db.add(row)
        db.flush()
    return row

def sync_uisp(db: Session):
    state = get_integration(db)
    try:
        if not settings.uisp_base_url or not settings.uisp_token:
            raise RuntimeError("UISP_BASE_URL or UISP_TOKEN is not configured")
        url = settings.uisp_base_url.rstrip("/") + "/nms/api/v2.1/devices"
        response = httpx.get(url, headers={"x-auth-token": settings.uisp_token}, verify=settings.uisp_verify_tls, timeout=45)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("UISP returned an unexpected response")
        count = 0
        for raw in payload:
            ident = raw.get("identification") or {}
            overview = raw.get("overview") or {}
            location = raw.get("location") or {}
            site = ident.get("site") or {}
            external_id = ident.get("id")
            if not external_id:
                continue
            device = db.scalar(select(Device).where(Device.source == "uisp", Device.external_id == external_id))
            if not device:
                device = Device(source="uisp", external_id=external_id, name=external_id)
                db.add(device)
            device.name = ident.get("displayName") or ident.get("name") or external_id
            device.hostname = ident.get("hostname")
            device.model = ident.get("model")
            device.model_name = ident.get("modelName")
            device.vendor = ident.get("vendor") or ident.get("vendorName")
            device.category = ident.get("category")
            device.device_type = ident.get("type")
            device.role = ident.get("role")
            device.status = overview.get("status") or ("active" if ident.get("authorized") else "unauthorized")
            device.authorized = bool(ident.get("authorized"))
            device.enabled = bool(raw.get("enabled", True))
            device.ip_address = raw.get("ipAddress")
            device.mac_address = ident.get("mac")
            device.firmware_version = ident.get("firmwareVersion")
            device.site_id = site.get("id")
            device.site_name = site.get("name")
            device.latitude = location.get("latitude")
            device.longitude = location.get("longitude")
            device.signal = overview.get("signal")
            device.cpu = overview.get("cpu")
            device.ram = overview.get("ram")
            device.frequency = overview.get("frequency")
            device.channel_width = overview.get("channelWidth")
            device.uplink_capacity = overview.get("uplinkCapacity")
            device.downlink_capacity = overview.get("downlinkCapacity")
            device.last_seen = parse_dt(overview.get("lastSeen"))
            device.raw_data = raw
            count += 1
        state.status = "connected"
        state.last_sync_at = datetime.now(timezone.utc)
        state.last_error = None
        state.last_item_count = count
        db.commit()
        return {"ok": True, "imported": count}
    except Exception as exc:
        db.rollback()
        state = get_integration(db)
        state.status = "error"
        state.last_sync_at = datetime.now(timezone.utc)
        state.last_error = str(exc)
        db.commit()
        raise
