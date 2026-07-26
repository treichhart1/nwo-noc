from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.integrations.uisp import sync_uisp
from app.services.monitoring import check_device
from app.models.entities import AuditLog, Device, Incident, Integration, MaintenanceWindow, MonitorCheck, MonitorEvent, NetworkLink, Sector, Site, TelemetrySample, User
from app.schemas.common import (
    AlertOut, DashboardOut, DeviceOut, DevicePatch, IntegrationOut, NetworkLinkCreate,
    NetworkLinkOut, SectorCreate, SectorOut, SiteCreate, SiteOut, SiteUpdate, Token,
    TopologyEdge, TopologyNode, TopologyOut, UserOut, IncidentCreate, IncidentUpdate, IncidentOut,
    MaintenanceCreate, MaintenanceOut, TelemetryCreate, TelemetryOut, OperationsSummary, MonitorCheckOut, MonitorEventOut, MonitoringSummary,
)

router = APIRouter(prefix="/api")
OFFLINE = ("offline", "unreachable", "disconnected")

def normalized_status(status: str | None) -> str:
    value = (status or "unknown").lower()
    if value in OFFLINE:
        return "offline"
    if value in ("active", "online", "connected"):
        return "active"
    return value

def site_out(site: Site, devices: list[Device]) -> SiteOut:
    members = [d for d in devices if d.wisp_site_id == site.id or (not d.wisp_site_id and d.site_name == site.name)]
    active = sum(normalized_status(x.status) == "active" for x in members)
    offline = sum(normalized_status(x.status) == "offline" for x in members)
    unauthorized = sum(not x.authorized for x in members)
    data = {c.name: getattr(site, c.name) for c in site.__table__.columns}
    return SiteOut(**data, device_count=len(members), active_count=active, offline_count=offline,
                   unauthorized_count=unauthorized, health_score=round(active / len(members) * 100) if members else 100)

def audit(db: Session, user: User, request: Request, action: str, entity_type: str, entity_id: str, details: dict):
    db.add(AuditLog(user_id=user.id, action=action, entity_type=entity_type, entity_id=entity_id,
                    details=details, ip_address=request.client.host if request.client else None))

@router.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    return Token(access_token=create_access_token(user.username))

@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user

@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db), _=Depends(current_user)):
    total = db.scalar(select(func.count()).select_from(Device)) or 0
    active = db.scalar(select(func.count()).select_from(Device).where(Device.status.in_(["active", "online", "connected"]))) or 0
    unauthorized = db.scalar(select(func.count()).select_from(Device).where(Device.authorized.is_(False))) or 0
    offline = db.scalar(select(func.count()).select_from(Device).where(Device.status.in_(OFFLINE))) or 0
    warning = max(total - active - offline, 0)
    mapped = db.scalar(select(func.count()).select_from(Device).where(Device.latitude.is_not(None), Device.longitude.is_not(None))) or 0
    sites = db.scalar(select(func.count()).select_from(Site)) or 0
    links = db.scalar(select(func.count()).select_from(NetworkLink)) or 0
    sectors = db.scalar(select(func.count()).select_from(Sector)) or 0
    subscribers = db.scalar(select(func.coalesce(func.sum(Site.customer_count), 0))) or 0
    integrations = list(db.scalars(select(Integration).order_by(Integration.name)))
    # Unauthorized radios are inventory/configuration work, not downtime. Health measures reachability only.
    health = round(((total - offline) / total) * 100) if total else 100
    return DashboardOut(total_devices=total, active_devices=active, unauthorized_devices=unauthorized,
        offline_devices=offline, warning_devices=warning, mapped_devices=mapped, sites=sites, links=links,
        sectors=sectors, subscribers=subscribers, health_score=health, integrations=integrations)

@router.get("/devices", response_model=list[DeviceOut])
def devices(q: str | None = Query(default=None), status: str | None = None, authorized: bool | None = None,
            site: str | None = None, limit: int = Query(500, ge=1, le=2000), db: Session = Depends(get_db), _=Depends(current_user)):
    stmt = select(Device)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Device.name.ilike(like), Device.ip_address.ilike(like), Device.mac_address.ilike(like), Device.site_name.ilike(like), Device.model_name.ilike(like)))
    if status: stmt = stmt.where(Device.status == status)
    if authorized is not None: stmt = stmt.where(Device.authorized.is_(authorized))
    if site: stmt = stmt.where(or_(Device.site_name == site, Device.wisp_site_id == int(site) if site.isdigit() else False))
    return list(db.scalars(stmt.order_by(Device.name).limit(limit)))

@router.get("/devices/{device_id}", response_model=DeviceOut)
def device(device_id: int, db: Session = Depends(get_db), _=Depends(current_user)):
    item = db.get(Device, device_id)
    if not item: raise HTTPException(status_code=404, detail="Device not found")
    return item

@router.patch("/devices/{device_id}", response_model=DeviceOut)
def patch_device(device_id: int, payload: DevicePatch, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.get(Device, device_id)
    if not item: raise HTTPException(status_code=404, detail="Device not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items(): setattr(item, key, value)
    if item.wisp_site_id:
        site = db.get(Site, item.wisp_site_id)
        if site: item.site_name = site.name
    audit(db, user, request, "device.update", "device", str(item.id), changes)
    db.commit(); db.refresh(item)
    return item

@router.get("/sites", response_model=list[SiteOut])
def sites(db: Session = Depends(get_db), _=Depends(current_user)):
    site_rows = list(db.scalars(select(Site).order_by(Site.name)))
    device_rows = list(db.scalars(select(Device)))
    return sorted([site_out(s, device_rows) for s in site_rows], key=lambda x: (x.offline_count == 0, x.name.lower()))

@router.post("/sites", response_model=SiteOut, status_code=201)
def create_site(payload: SiteCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = Site(**payload.model_dump())
    db.add(item)
    try: db.flush()
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail="A site with this name already exists") from exc
    audit(db, user, request, "site.create", "site", str(item.id), payload.model_dump())
    db.commit(); db.refresh(item)
    return site_out(item, [])

@router.get("/sites/{site_id}", response_model=SiteOut)
def get_site(site_id: int, db: Session = Depends(get_db), _=Depends(current_user)):
    item = db.get(Site, site_id)
    if not item: raise HTTPException(status_code=404, detail="Site not found")
    return site_out(item, list(db.scalars(select(Device))))

@router.put("/sites/{site_id}", response_model=SiteOut)
def update_site(site_id: int, payload: SiteUpdate, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.get(Site, site_id)
    if not item: raise HTTPException(status_code=404, detail="Site not found")
    changes = payload.model_dump()
    for key, value in changes.items(): setattr(item, key, value)
    for d in db.scalars(select(Device).where(Device.wisp_site_id == item.id)): d.site_name = item.name
    audit(db, user, request, "site.update", "site", str(item.id), changes)
    db.commit(); db.refresh(item)
    return site_out(item, list(db.scalars(select(Device))))

@router.delete("/sites/{site_id}", status_code=204)
def delete_site(site_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.get(Site, site_id)
    if not item: raise HTTPException(status_code=404, detail="Site not found")
    if db.scalar(select(func.count()).select_from(NetworkLink).where(or_(NetworkLink.a_site_id == site_id, NetworkLink.z_site_id == site_id))):
        raise HTTPException(status_code=409, detail="Remove this site's network links before deleting it")
    for d in db.scalars(select(Device).where(Device.wisp_site_id == site_id)): d.wisp_site_id = None
    audit(db, user, request, "site.delete", "site", str(item.id), {"name": item.name})
    db.delete(item); db.commit()
    return Response(status_code=204)

@router.get("/links", response_model=list[NetworkLinkOut])
def links(db: Session = Depends(get_db), _=Depends(current_user)):
    site_names = {s.id: s.name for s in db.scalars(select(Site))}
    result=[]
    for item in db.scalars(select(NetworkLink).order_by(NetworkLink.name)):
        data={c.name:getattr(item,c.name) for c in item.__table__.columns}
        result.append(NetworkLinkOut(**data, a_site_name=site_names.get(item.a_site_id), z_site_name=site_names.get(item.z_site_id)))
    return result

@router.post("/links", response_model=NetworkLinkOut, status_code=201)
def create_link(payload: NetworkLinkCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if payload.a_site_id == payload.z_site_id: raise HTTPException(status_code=422, detail="A and Z sites must be different")
    a,z=db.get(Site,payload.a_site_id),db.get(Site,payload.z_site_id)
    if not a or not z: raise HTTPException(status_code=404, detail="One or both link sites were not found")
    item=NetworkLink(**payload.model_dump()); db.add(item)
    try: db.flush()
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail="A link with this name already exists") from exc
    audit(db,user,request,"link.create","network_link",str(item.id),payload.model_dump())
    db.commit(); db.refresh(item)
    data={c.name:getattr(item,c.name) for c in item.__table__.columns}
    return NetworkLinkOut(**data,a_site_name=a.name,z_site_name=z.name)

@router.put("/links/{link_id}", response_model=NetworkLinkOut)
def update_link(link_id:int,payload:NetworkLinkCreate,request:Request,db:Session=Depends(get_db),user:User=Depends(current_user)):
    item=db.get(NetworkLink,link_id)
    if not item: raise HTTPException(status_code=404,detail="Link not found")
    if payload.a_site_id == payload.z_site_id: raise HTTPException(status_code=422, detail="A and Z sites must be different")
    for k,v in payload.model_dump().items(): setattr(item,k,v)
    audit(db,user,request,"link.update","network_link",str(item.id),payload.model_dump())
    db.commit(); db.refresh(item)
    a,z=db.get(Site,item.a_site_id),db.get(Site,item.z_site_id)
    data={c.name:getattr(item,c.name) for c in item.__table__.columns}
    return NetworkLinkOut(**data,a_site_name=a.name if a else None,z_site_name=z.name if z else None)

@router.delete("/links/{link_id}", status_code=204)
def delete_link(link_id:int,request:Request,db:Session=Depends(get_db),user:User=Depends(current_user)):
    item=db.get(NetworkLink,link_id)
    if not item: raise HTTPException(status_code=404,detail="Link not found")
    audit(db,user,request,"link.delete","network_link",str(item.id),{"name":item.name}); db.delete(item); db.commit()
    return Response(status_code=204)

@router.get("/sectors", response_model=list[SectorOut])
def sectors(db:Session=Depends(get_db),_=Depends(current_user)):
    names={s.id:s.name for s in db.scalars(select(Site))}; result=[]
    for item in db.scalars(select(Sector).order_by(Sector.name)):
        data={c.name:getattr(item,c.name) for c in item.__table__.columns}; result.append(SectorOut(**data,site_name=names.get(item.site_id)))
    return result

@router.post("/sectors", response_model=SectorOut, status_code=201)
def create_sector(payload:SectorCreate,request:Request,db:Session=Depends(get_db),user:User=Depends(current_user)):
    site=db.get(Site,payload.site_id)
    if not site: raise HTTPException(status_code=404,detail="Site not found")
    item=Sector(**payload.model_dump()); db.add(item)
    try: db.flush()
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(status_code=409,detail="A sector with this name already exists") from exc
    audit(db,user,request,"sector.create","sector",str(item.id),payload.model_dump()); db.commit(); db.refresh(item)
    data={c.name:getattr(item,c.name) for c in item.__table__.columns}; return SectorOut(**data,site_name=site.name)

@router.delete("/sectors/{sector_id}", status_code=204)
def delete_sector(sector_id:int,request:Request,db:Session=Depends(get_db),user:User=Depends(current_user)):
    item=db.get(Sector,sector_id)
    if not item: raise HTTPException(status_code=404,detail="Sector not found")
    audit(db,user,request,"sector.delete","sector",str(item.id),{"name":item.name}); db.delete(item); db.commit()
    return Response(status_code=204)

@router.get("/alerts", response_model=list[AlertOut])
def alerts(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), _=Depends(current_user)):
    items = list(db.scalars(select(Device).where(or_(Device.status.in_(OFFLINE), Device.authorized.is_(False))).order_by(Device.updated_at.desc()).limit(limit)))
    result=[]
    for item in items:
        offline=normalized_status(item.status)=="offline"
        result.append(AlertOut(id=f"device-{item.id}-{'offline' if offline else 'unauthorized'}",severity="critical" if offline else "warning",
            title=f"{item.name} {'offline' if offline else 'requires authorization'}",
            message=(f"Device is {item.status or 'unreachable'} at {item.site_name or 'an unassigned site'}." if offline else "UISP reports this device as unauthorized."),
            device_id=item.id,device_name=item.name,site_name=item.site_name,created_at=item.updated_at))
    return result

@router.get("/topology", response_model=TopologyOut)
def topology(db: Session = Depends(get_db), _=Depends(current_user)):
    sites=list(db.scalars(select(Site).order_by(Site.name))); devices=list(db.scalars(select(Device).order_by(Device.name).limit(2000)))
    links=list(db.scalars(select(NetworkLink).order_by(NetworkLink.name)))
    nodes=[TopologyNode(id=f"site:{s.id}",label=s.name,kind="site",status=s.status,site_name=s.name) for s in sites]
    edges=[TopologyEdge(source=f"site:{l.a_site_id}",target=f"site:{l.z_site_id}",label=f"{l.link_type} · {l.capacity_mbps:g} Mbps" if l.capacity_mbps else l.link_type,status=l.status) for l in links]
    for d in devices:
        sid=d.wisp_site_id
        if sid:
            nodes.append(TopologyNode(id=f"device:{d.id}",label=d.name,kind="device",status=normalized_status(d.status),site_name=d.site_name,device_id=d.id))
            edges.append(TopologyEdge(source=f"device:{d.parent_device_id}" if d.parent_device_id else f"site:{sid}",target=f"device:{d.id}"))
    return TopologyOut(nodes=nodes,edges=edges)

@router.get("/integrations", response_model=list[IntegrationOut])
def integrations(db: Session = Depends(get_db), _=Depends(current_user)):
    return list(db.scalars(select(Integration).order_by(Integration.name)))

@router.post("/integrations/uisp/sync")
def trigger_sync(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        result=sync_uisp(db)
        # Attach synced devices to existing WISP site records by exact site name.
        sites={s.name:s.id for s in db.scalars(select(Site))}
        for d in db.scalars(select(Device).where(Device.site_name.is_not(None))):
            if d.site_name in sites and not d.wisp_site_id: d.wisp_site_id=sites[d.site_name]
        audit(db,user,request,"uisp.sync","integration","uisp",result); db.commit(); return result
    except Exception as exc:
        raise HTTPException(status_code=502,detail=f"UISP synchronization failed: {exc}") from exc


def incident_out(item: Incident, db: Session) -> IncidentOut:
    site = db.get(Site, item.site_id) if item.site_id else None
    device = db.get(Device, item.device_id) if item.device_id else None
    link = db.get(NetworkLink, item.link_id) if item.link_id else None
    data = {c.name: getattr(item, c.name) for c in item.__table__.columns}
    return IncidentOut(**data, site_name=site.name if site else None,
                       device_name=device.name if device else None,
                       link_name=link.name if link else None)

@router.get("/operations/summary", response_model=OperationsSummary)
def operations_summary(db: Session = Depends(get_db), _=Depends(current_user)):
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=15)
    open_incidents = db.scalar(select(func.count()).select_from(Incident).where(Incident.status != "resolved")) or 0
    critical_incidents = db.scalar(select(func.count()).select_from(Incident).where(Incident.status != "resolved", Incident.severity == "critical")) or 0
    impacted_customers = db.scalar(select(func.coalesce(func.sum(Incident.customer_impact), 0)).where(Incident.status != "resolved")) or 0
    upcoming = db.scalar(select(func.count()).select_from(MaintenanceWindow).where(MaintenanceWindow.status == "scheduled", MaintenanceWindow.ends_at >= now)) or 0
    offline_site_ids = db.scalars(select(Device.wisp_site_id).where(Device.wisp_site_id.is_not(None), Device.status.in_(OFFLINE)).distinct()).all()
    degraded_links = db.scalar(select(func.count()).select_from(NetworkLink).where(NetworkLink.status.not_in(("active", "online")))) or 0
    unassigned = db.scalar(select(func.count()).select_from(Device).where(Device.wisp_site_id.is_(None))) or 0
    stale = db.scalar(select(func.count()).select_from(Device).where(or_(Device.last_seen.is_(None), Device.last_seen < stale_before))) or 0
    return OperationsSummary(open_incidents=open_incidents, critical_incidents=critical_incidents,
        impacted_customers=int(impacted_customers), upcoming_maintenance=upcoming,
        offline_sites=len(set(offline_site_ids)), degraded_links=degraded_links,
        unassigned_devices=unassigned, stale_devices=stale)

@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(status: str | None = None, limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db), _=Depends(current_user)):
    stmt = select(Incident)
    if status:
        stmt = stmt.where(Incident.status == status)
    rows = list(db.scalars(stmt.order_by(Incident.status == "resolved", Incident.started_at.desc()).limit(limit)))
    return [incident_out(x, db) for x in rows]

@router.post("/incidents", response_model=IncidentOut, status_code=201)
def create_incident(payload: IncidentCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = payload.model_dump()
    data["started_at"] = data.get("started_at") or datetime.now(timezone.utc)
    item = Incident(**data, created_by=user.id)
    db.add(item); db.flush()
    audit(db, user, request, "incident.create", "incident", str(item.id), payload.model_dump(mode="json"))
    db.commit(); db.refresh(item)
    return incident_out(item, db)

@router.patch("/incidents/{incident_id}", response_model=IncidentOut)
def update_incident(incident_id: int, payload: IncidentUpdate, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.get(Incident, incident_id)
    if not item:
        raise HTTPException(status_code=404, detail="Incident not found")
    changes = payload.model_dump(exclude_unset=True)
    old_status = item.status
    for key, value in changes.items():
        setattr(item, key, value)
    if changes.get("status") == "acknowledged" and not item.acknowledged_at:
        item.acknowledged_at = datetime.now(timezone.utc)
    if changes.get("status") == "resolved" and not item.resolved_at:
        item.resolved_at = datetime.now(timezone.utc)
    if old_status == "resolved" and changes.get("status") and changes["status"] != "resolved":
        item.resolved_at = None
    audit(db, user, request, "incident.update", "incident", str(item.id), changes)
    db.commit(); db.refresh(item)
    return incident_out(item, db)

@router.delete("/incidents/{incident_id}", status_code=204)
def delete_incident(incident_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.get(Incident, incident_id)
    if not item:
        raise HTTPException(status_code=404, detail="Incident not found")
    audit(db, user, request, "incident.delete", "incident", str(item.id), {"title": item.title})
    db.delete(item); db.commit()
    return Response(status_code=204)

@router.get("/maintenance", response_model=list[MaintenanceOut])
def list_maintenance(db: Session = Depends(get_db), _=Depends(current_user)):
    site_names = {s.id:s.name for s in db.scalars(select(Site))}
    link_names = {l.id:l.name for l in db.scalars(select(NetworkLink))}
    result=[]
    for item in db.scalars(select(MaintenanceWindow).order_by(MaintenanceWindow.starts_at.desc())):
        data={c.name:getattr(item,c.name) for c in item.__table__.columns}
        result.append(MaintenanceOut(**data, site_name=site_names.get(item.site_id), link_name=link_names.get(item.link_id)))
    return result

@router.post("/maintenance", response_model=MaintenanceOut, status_code=201)
def create_maintenance(payload: MaintenanceCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if payload.ends_at <= payload.starts_at:
        raise HTTPException(status_code=422, detail="Maintenance end time must be after start time")
    item=MaintenanceWindow(**payload.model_dump(), created_by=user.id); db.add(item); db.flush()
    audit(db,user,request,"maintenance.create","maintenance",str(item.id),payload.model_dump(mode="json"))
    db.commit(); db.refresh(item)
    data={c.name:getattr(item,c.name) for c in item.__table__.columns}
    site=db.get(Site,item.site_id) if item.site_id else None; link=db.get(NetworkLink,item.link_id) if item.link_id else None
    return MaintenanceOut(**data,site_name=site.name if site else None,link_name=link.name if link else None)

@router.patch("/maintenance/{maintenance_id}/status", response_model=MaintenanceOut)
def update_maintenance_status(maintenance_id:int,status:str,request:Request,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if status not in {"scheduled","in_progress","completed","cancelled"}:
        raise HTTPException(status_code=422,detail="Invalid maintenance status")
    item=db.get(MaintenanceWindow,maintenance_id)
    if not item: raise HTTPException(status_code=404,detail="Maintenance window not found")
    item.status=status; audit(db,user,request,"maintenance.status","maintenance",str(item.id),{"status":status}); db.commit(); db.refresh(item)
    data={c.name:getattr(item,c.name) for c in item.__table__.columns}
    site=db.get(Site,item.site_id) if item.site_id else None; link=db.get(NetworkLink,item.link_id) if item.link_id else None
    return MaintenanceOut(**data,site_name=site.name if site else None,link_name=link.name if link else None)

@router.post("/devices/{device_id}/telemetry", response_model=TelemetryOut, status_code=201)
def ingest_telemetry(device_id:int,payload:TelemetryCreate,db:Session=Depends(get_db),_=Depends(current_user)):
    device=db.get(Device,device_id)
    if not device: raise HTTPException(status_code=404,detail="Device not found")
    data=payload.model_dump(); data["sampled_at"]=data.get("sampled_at") or datetime.now(timezone.utc)
    item=TelemetrySample(device_id=device_id,**data); db.add(item)
    for src,dst in [("cpu_percent","cpu"),("ram_percent","ram"),("signal_dbm","signal")]:
        value=getattr(item,src)
        if value is not None: setattr(device,dst,value)
    device.last_seen=item.sampled_at
    db.commit(); db.refresh(item); return item

@router.get("/devices/{device_id}/telemetry", response_model=list[TelemetryOut])
def device_telemetry(device_id:int,hours:int=Query(24,ge=1,le=720),limit:int=Query(500,ge=1,le=5000),db:Session=Depends(get_db),_=Depends(current_user)):
    if not db.get(Device,device_id): raise HTTPException(status_code=404,detail="Device not found")
    since=datetime.now(timezone.utc)-timedelta(hours=hours)
    return list(db.scalars(select(TelemetrySample).where(TelemetrySample.device_id==device_id,TelemetrySample.sampled_at>=since).order_by(TelemetrySample.sampled_at.desc()).limit(limit)))


@router.get("/monitoring/summary", response_model=MonitoringSummary)
def monitoring_summary(db: Session = Depends(get_db), _=Depends(current_user)):
    enabled = db.scalar(select(func.count()).select_from(Device).where(Device.monitor_enabled.is_(True))) or 0
    up = db.scalar(select(func.count()).select_from(Device).where(Device.monitor_enabled.is_(True), Device.monitor_status == "up")) or 0
    down = db.scalar(select(func.count()).select_from(Device).where(Device.monitor_enabled.is_(True), Device.monitor_status == "down")) or 0
    unknown = max(enabled - up - down, 0)
    avg = db.scalar(select(func.avg(Device.monitor_latency_ms)).where(Device.monitor_enabled.is_(True), Device.monitor_status == "up"))
    events = db.scalar(select(func.count()).select_from(MonitorEvent).where(MonitorEvent.acknowledged.is_(False))) or 0
    last = db.scalar(select(func.max(Device.monitor_last_checked_at)).where(Device.monitor_enabled.is_(True)))
    return MonitoringSummary(enabled_devices=enabled, up_devices=up, down_devices=down, unknown_devices=unknown,
        average_latency_ms=round(float(avg), 2) if avg is not None else None, unacknowledged_events=events, last_check_at=last)

@router.post("/devices/{device_id}/monitor/check", response_model=MonitorCheckOut)
def run_monitor_check(device_id: int, db: Session = Depends(get_db), _=Depends(current_user)):
    device = db.get(Device, device_id)
    if not device: raise HTTPException(status_code=404, detail="Device not found")
    return check_device(db, device, force=True)

@router.get("/devices/{device_id}/monitor/checks", response_model=list[MonitorCheckOut])
def monitor_history(device_id: int, hours: int = Query(24, ge=1, le=720), limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db), _=Depends(current_user)):
    if not db.get(Device, device_id): raise HTTPException(status_code=404, detail="Device not found")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return list(db.scalars(select(MonitorCheck).where(MonitorCheck.device_id == device_id, MonitorCheck.checked_at >= since).order_by(MonitorCheck.checked_at.desc()).limit(limit)))

@router.get("/monitoring/events", response_model=list[MonitorEventOut])
def monitor_events(acknowledged: bool | None = None, limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db), _=Depends(current_user)):
    stmt = select(MonitorEvent)
    if acknowledged is not None: stmt = stmt.where(MonitorEvent.acknowledged.is_(acknowledged))
    rows = list(db.scalars(stmt.order_by(MonitorEvent.created_at.desc()).limit(limit)))
    devices = {d.id:d for d in db.scalars(select(Device).where(Device.id.in_([r.device_id for r in rows])))} if rows else {}
    result=[]
    for row in rows:
        data={c.name:getattr(row,c.name) for c in row.__table__.columns}; d=devices.get(row.device_id)
        result.append(MonitorEventOut(**data, device_name=d.name if d else None, site_name=d.site_name if d else None))
    return result

@router.patch("/monitoring/events/{event_id}/acknowledge", response_model=MonitorEventOut)
def acknowledge_monitor_event(event_id: int, db: Session = Depends(get_db), _=Depends(current_user)):
    item = db.get(MonitorEvent, event_id)
    if not item: raise HTTPException(status_code=404, detail="Monitoring event not found")
    item.acknowledged = True; db.commit(); db.refresh(item)
    device = db.get(Device, item.device_id); data={c.name:getattr(item,c.name) for c in item.__table__.columns}
    return MonitorEventOut(**data, device_name=device.name if device else None, site_name=device.site_name if device else None)
