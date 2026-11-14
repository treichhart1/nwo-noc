"""Shared incident updates: existing and RF operations UIs use the same records."""
from datetime import datetime,timezone
from app.models.entities import User,IncidentNote,Sector
from app.core.operator_text import clean_operator_text


def apply_update(db,item,changes,*,user_id,now=None):
    now=now or datetime.now(timezone.utc);changes=dict(changes)
    expected=changes.pop('expected_revision',None)
    if expected is not None and expected!=item.revision:raise ValueError('Investigation changed; reload before saving your notes')
    sector_id=changes.get('sector_id',item.sector_id)
    if sector_id:
        sector=db.get(Sector,sector_id)
        if not sector or changes.get('site_id',item.site_id) and changes.get('site_id',item.site_id)!=sector.site_id or changes.get('device_id',item.device_id) and changes.get('device_id',item.device_id)!=sector.device_id:raise ValueError('Sector, site and device context must match existing inventory')
    assigned=changes.get('assigned_to_user_id')
    if assigned is not None:
        user=db.get(User,assigned)
        if user is None or not user.is_active or user.role not in {'admin','operator'}:raise ValueError('Assignee must be an active operator or administrator')
    if 'status' in changes and changes['status'] is None:raise ValueError('Incident status cannot be null')
    status=changes.get('status',item.status)
    resolution=changes.get('resolution',item.resolution)
    if status=='closed' and (item.status not in {'resolved','closed'} or not resolution or not resolution.strip()):
        raise ValueError('Resolve and document the investigation before closing it')
    if item.status=='closed' and status!='closed':raise ValueError('Closed investigations cannot be silently reopened; create a reviewed new incident')
    for field in ('note','findings','resolution'):
        if field in changes:changes[field]=clean_operator_text(changes[field])
    note=changes.pop('note',None)
    old=item.status
    for key,value in changes.items():setattr(item,key,value)
    if status=='acknowledged' and not item.acknowledged_at:item.acknowledged_at=now
    if status=='resolved' and not item.resolved_at:item.resolved_at=now
    if status=='closed' and not item.closed_at:item.closed_at=now
    if old=='resolved' and status not in {'resolved','closed'}:item.resolved_at=None
    item.revision+=1;item.updated_at=now
    if note:db.add(IncidentNote(incident_id=item.id,user_id=user_id,body=note))
    return {**changes,'note_added':bool(note),'from_status':old,'revision':item.revision}
