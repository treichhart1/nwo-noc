"""Potential dependency impact, not a claim about end-user reachability."""

from collections import defaultdict, deque
from dataclasses import dataclass
from sqlalchemy import select
from app.models.entities import (
    Site,
    Device,
    Sector,
    NetworkLink,
    Incident,
    MaintenanceWindow,
)
from app.customers.models import CustomerService, CustomerAccount, IncidentCustomer
from app.customers.contracts import e164, masked_phone
from datetime import datetime, timezone


@dataclass(frozen=True)
class Target:
    site_id: int | None = None
    device_id: int | None = None
    sector_id: int | None = None
    link_id: int | None = None


def target_for(db, *, incident_id=None, maintenance_id=None, target=None):
    if incident_id:
        row = db.get(Incident, incident_id)
        if row is None:
            raise LookupError()
        # A location field is context, not proof that the whole tower failed.
        if row.link_id:
            return Target(link_id=row.link_id)
        if row.sector_id:return Target(sector_id=row.sector_id)
        if row.device_id:
            return Target(device_id=row.device_id)
        return Target(site_id=row.site_id)
    if maintenance_id:
        row = db.get(MaintenanceWindow, maintenance_id)
        if row is None:
            raise LookupError()
        return (
            Target(link_id=row.link_id) if row.link_id else Target(site_id=row.site_id)
        )
    target = target or Target()
    for ident, model in (
        (target.site_id, Site),
        (target.device_id, Device),
        (target.sector_id, Sector),
        (target.link_id, NetworkLink),
    ):
        if ident and db.get(model, ident) is None:
            raise LookupError()
    return target


def reachable(roots, links, excluded_sites=(), excluded_links=(), allow_unknown=False):
    graph = defaultdict(set)
    for link in links:
        if (
            link.id in excluded_links
            or link.a_site_id in excluded_sites
            or link.z_site_id in excluded_sites
        ):
            continue
        if link.status not in {"active", "up", "online"} and not (
            allow_unknown and link.status not in {"down", "offline", "failed"}
        ):
            continue
        graph[link.a_site_id].add(link.z_site_id)
        graph[link.z_site_id].add(link.a_site_id)
    seen = set(roots) - set(excluded_sites)
    queue = deque(seen)
    while queue:
        for child in graph[queue.popleft()] - seen:
            seen.add(child)
            queue.append(child)
    return seen


def evidence_path(roots, links, destination, excluded_sites=(), excluded_links=()):
    graph = defaultdict(list)
    for link in links:
        if (
            link.id in excluded_links
            or link.a_site_id in excluded_sites
            or link.z_site_id in excluded_sites
            or link.status not in {"active", "up", "online"}
        ):
            continue
        graph[link.a_site_id].append((link.z_site_id, link.id))
        graph[link.z_site_id].append((link.a_site_id, link.id))
    parents = {r: None for r in roots if r not in excluded_sites}
    queue = deque(sorted(parents))
    while queue:
        node = queue.popleft()
        if node == destination:
            nodes = [node]
            edges = []
            while parents[node] is not None:
                node, edge = parents[node]
                nodes.append(node)
                edges.append(edge)
            return {"site_ids": nodes[::-1], "link_ids": edges[::-1]}
        for neighbor, edge in sorted(graph[node]):
            if neighbor not in parents:
                parents[neighbor] = (node, edge)
                queue.append(neighbor)
    return None


def analyze(db, target, services):
    sites = {s.id: s for s in db.scalars(select(Site))}
    links = list(db.scalars(select(NetworkLink)))
    sectors = {s.id: s for s in db.scalars(select(Sector))}
    devices = {d.id: d for d in db.scalars(select(Device))}
    roots = {s.id for s in sites.values() if s.site_type == "core-pop"}
    excluded_sites = {target.site_id} if target.site_id else set()
    excluded_links = {target.link_id} if target.link_id else set()
    if target.device_id:
        excluded_links.update(
            l.id for l in links if target.device_id in (l.a_device_id, l.z_device_id)
        )
    # Router/power dependencies are not invented from mere co-location.
    before = reachable(roots, links)
    after = reachable(roots, links, excluded_sites, excluded_links)
    possible = reachable(roots, links, excluded_sites, excluded_links, True)
    path_cache = {}
    result = []
    for svc in services:
        classification, reason = "unknown", "unmapped_or_unreviewed"
        if svc.site_id not in path_cache:
            path_cache[svc.site_id] = (
                evidence_path(roots, links, svc.site_id),
                evidence_path(
                    roots, links, svc.site_id, excluded_sites, excluded_links
                ),
            )
        evidence = {
            "target": {k: v for k, v in vars(target).items() if v},
            "mapping_state": svc.mapping_state,
            "site_id": svc.site_id,
            "sector_id": svc.sector_id,
            "device_id": svc.device_id,
            "fiber_link_id": svc.fiber_link_id,
            "baseline_path": path_cache[svc.site_id][0],
            "remaining_path": path_cache[svc.site_id][1],
            "excluded_link_ids": sorted(excluded_links),
            "claim": "potential dependency impact only",
        }
        sector = sectors.get(svc.sector_id)
        valid = (
            svc.mapping_state == "verified"
            and svc.site_id in sites
            and (
                not svc.device_id
                or svc.device_id in devices
                and devices[svc.device_id].wisp_site_id == svc.site_id
            )
            and (
                not svc.sector_id
                or sector
                and sector.site_id == svc.site_id
                and (not svc.device_id or sector.device_id == svc.device_id)
            )
            and (
                not svc.fiber_link_id
                or any(
                    l.id == svc.fiber_link_id
                    and svc.site_id in (l.a_site_id, l.z_site_id)
                    for l in links
                )
            )
        )
        if svc.source_deleted_at or svc.status != "active":
            classification, reason = "not_affected", "inactive_service"
        elif (
            datetime.now(timezone.utc)
            - (
                svc.last_synced_at.replace(tzinfo=timezone.utc)
                if svc.last_synced_at.tzinfo is None
                else svc.last_synced_at
            )
        ).total_seconds() > 86400:
            reason = "stale_customer_data"
        elif valid:
            direct = (
                target.site_id
                and target.site_id == svc.site_id
                or target.device_id
                and (
                    target.device_id == svc.device_id
                    or sector
                    and sector.device_id == target.device_id
                )
                or target.sector_id
                and target.sector_id == svc.sector_id
            )
            if direct:
                classification, reason = (
                    ("probable", "hybrid_failover_unverified")
                    if svc.service_type == "hybrid" and not target.site_id
                    else ("confirmed", "explicit_serving_dependency")
                )
            elif target.link_id and target.link_id == svc.fiber_link_id:
                classification, reason = (
                    ("probable", "hybrid_failover_unverified")
                    if svc.service_type == "hybrid"
                    else ("confirmed", "explicit_fiber_dependency")
                )
            elif (
                (svc.service_type in {"fiber", "hybrid"} and not svc.fiber_link_id)
                or (
                    svc.service_type in {"wireless", "hybrid"}
                    and not (svc.device_id or svc.sector_id)
                )
                or svc.service_type == "unknown"
            ):
                reason = "incomplete_access_dependencies"
            elif (target.site_id or excluded_links) and roots and svc.site_id in before:
                if svc.site_id in after:
                    classification, reason = "not_affected", "remaining_recorded_path"
                elif svc.site_id in possible:
                    classification, reason = "unknown", "alternate_path_health_unknown"
                else:
                    classification, reason = "probable", "recorded_upstream_path_lost"
            elif (
                target.sector_id
                or (
                    target.device_id
                    and devices.get(target.device_id)
                    and devices[target.device_id].role == "access-point"
                )
            ) and (
                (svc.service_type == "wireless" and (svc.device_id or svc.sector_id))
                or svc.service_type == "fiber"
                and svc.fiber_link_id
            ):
                classification, reason = (
                    "not_affected",
                    "different_explicit_access_dependency",
                )
            else:
                reason = "insufficient_topology_evidence"
        result.append(
            {
                "service_id": svc.id,
                "customer_id": svc.customer_id,
                "classification": classification,
                "reason": reason,
                "evidence": evidence,
            }
        )
    return result


def persist_incident(db, incident_id, results):
    existing = {
        r.service_id: r
        for r in db.scalars(
            select(IncidentCustomer).where(IncidentCustomer.incident_id == incident_id)
        )
    }
    now = datetime.now(timezone.utc)
    for item in results:
        row = existing.get(item["service_id"])
        if row is None:
            row = IncidentCustomer(
                incident_id=incident_id, service_id=item["service_id"]
            )
            db.add(row)
        row.classification = item["classification"]
        row.evidence = {"reason": item["reason"], **item["evidence"]}
        row.evaluated_at = now


def recipients(db, results, *, reveal=False):
    """Notification selection only; conservative, no delivery function or SDK."""
    ids = {r["customer_id"] for r in results}
    accounts = (
        {
            c.id: c
            for c in db.scalars(
                select(CustomerAccount).where(CustomerAccount.id.in_(ids))
            )
        }
        if ids
        else {}
    )
    candidates = defaultdict(list)
    for r in results:
        if r["classification"] in {"confirmed", "probable"}:
            candidates[r["customer_id"]].append(r["service_id"])
    phone_accounts = defaultdict(set)
    for cid in candidates:
        number = e164(accounts[cid].phone)
        if number:
            phone_accounts[number].add(cid)
    # Suppress a shared number across the whole customer dataset, including
    # accounts on another preview page or with different communication choices.
    numbers = set(phone_accounts)
    if numbers:
        for cid, number in db.execute(
            select(CustomerAccount.id, CustomerAccount.phone).where(
                CustomerAccount.phone.in_(numbers)
            )
        ):
            phone_accounts[number].add(cid)
    output = []
    for cid, service_ids in sorted(candidates.items()):
        a = accounts[cid]
        number = e164(a.phone)
        reason = (
            "source_deleted"
            if a.source_deleted_at
            else (
                "invalid_or_missing_phone"
                if not number
                else (
                    "opted_out"
                    if a.sms_opt_out
                    else (
                        "consent_unverified"
                        if a.sms_consent != "granted" or not a.consent_record
                        else (
                            "preference_not_sms"
                            if a.communication_preference not in {"sms", "both"}
                            else (
                                "shared_number_review"
                                if len(phone_accounts[number]) > 1
                                else "eligible"
                            )
                        )
                    )
                )
            )
        )
        output.append(
            {
                "customer_id": cid,
                "service_ids": sorted(set(service_ids)),
                "phone": (
                    number if reveal and reason == "eligible" else masked_phone(a.phone)
                ),
                "eligible": reason == "eligible",
                "reason": reason,
            }
        )
    return {
        "delivery_enabled": False,
        "recipients": output,
        "eligible_count": sum(r["eligible"] for r in output),
        "unknown_impacts_excluded": True,
        "shared_numbers_suppressed": True,
    }
