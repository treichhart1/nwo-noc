"""Transactional documentation-only IPAM. No network transport or equipment writes."""

import csv, io, json, hashlib
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network, IPv4Address, IPv6Address
from sqlalchemy import select, func, text, or_, cast, String
from app.models.entities import Site, Device, NetworkLink, AuditLog, Sector
from app.customers.models import CustomerService, CustomerAccount
from app.core.operator_text import clean_operator_text
from .models import (
    VRF,
    VLAN,
    Interface,
    Prefix,
    Pool,
    Assignment,
    DNSRecord,
    Observation,
    ImportBatch,
    Change,
)
from .contracts import PrefixInput, AssignmentInput

ACTIVE = ("assigned", "reserved")


def clock():
    return datetime.now(timezone.utc)


def lock(db):
    db.execute(text("SET LOCAL statement_timeout='5000ms'"))
    db.execute(text("SELECT pg_advisory_xact_lock(990099)"))


def view(row):
    return {
        c.name: (
            getattr(row, c.name).isoformat()
            if isinstance(getattr(row, c.name), datetime)
            else (
                str(getattr(row, c.name))
                if c.name in {"address", "network", "gateway", "first", "last"}
                and getattr(row, c.name) is not None
                else getattr(row, c.name)
            )
        )
        for c in row.__table__.columns
    }


def checked_text(data):
    for value in data.values():
        if isinstance(value, str):
            clean_operator_text(value)


def record(db, actor, entity, action, reason, before=None):
    checked_text({"reason": reason})
    after = view(entity)
    db.add(
        Change(
            actor_id=actor,
            entity=entity.__tablename__,
            entity_id=entity.id,
            action=action,
            reason=reason,
            before=before or {},
            after=after,
        )
    )
    db.add(
        AuditLog(
            user_id=actor,
            action="ipam." + action,
            entity_type=entity.__tablename__,
            entity_id=str(entity.id),
            details={"documentation_only": True},
        )
    )


def required(db, model, ident):
    row = db.get(model, ident)
    if not row:
        raise ValueError("Referenced IPAM or inventory record is unavailable")
    return row


def bounds(net):
    n = ip_network(str(net))
    lo = int(n.network_address)
    hi = int(n.broadcast_address)
    if n.version == 4 and n.prefixlen < 31:
        lo += 1
        hi -= 1
    if n.version == 6 and n.prefixlen < 127:
        lo += 1  # subnet-router anycast is withheld
    return lo, hi


def create_vrf(db, b, actor):
    lock(db)
    data = b.model_dump(exclude={"reason"})
    checked_text(data)
    if db.scalar(select(VRF.id).where(VRF.name == b.name)):
        raise ValueError("Routing domain name already documented")
    row = VRF(**data)
    db.add(row)
    db.flush()
    record(db, actor, row, "vrf_created", b.reason)
    return row


def create_vlan(db, b, actor):
    lock(db)
    required(db, VRF, b.vrf_id)
    required(db, Site, b.site_id)
    data = b.model_dump(exclude={"reason"})
    checked_text(data)
    if db.scalar(
        select(VLAN.id).where(
            VLAN.vrf_id == b.vrf_id, VLAN.site_id == b.site_id, VLAN.vid == b.vid
        )
    ):
        raise ValueError("VLAN already exists in this tower and routing domain")
    row = VLAN(**data)
    db.add(row)
    db.flush()
    record(db, actor, row, "vlan_created", b.reason)
    return row


def create_interface(db, b, actor):
    lock(db)
    d = required(db, Device, b.device_id)
    required(db, VRF, b.vrf_id)
    if b.vlan_id:
        v = required(db, VLAN, b.vlan_id)
        if v.vrf_id != b.vrf_id or v.site_id != d.wisp_site_id:
            raise ValueError("VLAN/device routing domain or site mismatch")
    if b.parent_id:
        parent = required(db, Interface, b.parent_id)
        if parent.device_id != d.id or parent.vrf_id != b.vrf_id:
            raise ValueError("Parent interface mismatch")
    if db.scalar(
        select(Interface.id).where(
            Interface.device_id == d.id, Interface.external_id == b.external_id
        )
    ):
        raise ValueError("Interface identifier already documented")
    data = b.model_dump(exclude={"reason"})
    checked_text(data)
    row = Interface(**data)
    db.add(row)
    db.flush()
    record(db, actor, row, "interface_created", b.reason)
    return row


def validate_prefix(db, b):
    required(db, VRF, b.vrf_id)
    net = ip_network(b.network)
    if net.is_multicast or net.is_unspecified or net.is_loopback:
        raise ValueError("Unicast infrastructure/customer prefix required")
    if b.site_id:
        required(db, Site, b.site_id)
    if b.vlan_id:
        v = required(db, VLAN, b.vlan_id)
        if v.vrf_id != b.vrf_id or v.site_id != b.site_id:
            raise ValueError("VLAN prefix context mismatch")
    if b.link_id:
        link = required(db, NetworkLink, b.link_id)
        if b.site_id and b.site_id not in {link.a_site_id, link.z_site_id}:
            raise ValueError("Prefix site is not a link endpoint")
    parent = required(db, Prefix, b.parent_id) if b.parent_id else None
    ancestors = set()
    while parent:
        if parent.id in ancestors:
            raise ValueError("Prefix hierarchy cycle")
        ancestors.add(parent.id)
        pnet = ip_network(str(parent.network))
        if (
            parent.vrf_id != b.vrf_id
            or parent.kind != "block"
            or net.version != pnet.version
            or not net.subnet_of(pnet)
            or net == pnet
        ):
            raise ValueError(
                "Child must be strictly inside a block in the same routing domain"
            )
        parent = required(db, Prefix, parent.parent_id) if parent.parent_id else None
    overlaps = list(
        db.scalars(
            select(Prefix)
            .where(Prefix.vrf_id == b.vrf_id, Prefix.network.op("&&")(b.network))
            .limit(1001)
        )
    )
    if len(overlaps) > 1000 or any(p.id not in ancestors for p in overlaps):
        raise ValueError(
            "Overlap requires an explicit, nonoverlapping parent/child hierarchy"
        )
    if b.gateway:
        gw = ip_address(b.gateway)
        lo, hi = bounds(net)
        if gw.version != net.version or not lo <= int(gw) <= hi or b.kind != "subnet":
            raise ValueError("Gateway is not a usable address in this subnet")
    return net


def create_prefix(db, b, actor):
    lock(db)
    validate_prefix(db, b)
    data = b.model_dump(exclude={"reason"})
    checked_text(data)
    row = Prefix(**data)
    db.add(row)
    db.flush()
    record(db, actor, row, "prefix_created", b.reason)
    return row


def create_pool(db, b, actor):
    lock(db)
    p = required(db, Prefix, b.prefix_id)
    lo, hi = bounds(p.network)
    a, z = ip_address(b.first), ip_address(b.last)
    if (
        p.kind != "subnet"
        or a.version != ip_network(str(p.network)).version
        or z.version != a.version
        or not lo <= int(a) <= int(z) <= hi
    ):
        raise ValueError("Pool must be a usable ordered range in a leaf subnet")
    if p.gateway and int(a) <= int(ip_address(str(p.gateway))) <= int(z):
        raise ValueError("Pool overlaps the documented gateway")
    if db.scalar(
        select(Pool.id).where(
            Pool.prefix_id == p.id, Pool.first <= b.last, Pool.last >= b.first
        )
    ):
        raise ValueError("Overlapping address pool")
    data = b.model_dump(exclude={"reason"})
    checked_text(data)
    row = Pool(**data)
    db.add(row)
    db.flush()
    record(db, actor, row, "pool_created", b.reason)
    return row


def association(db, p, b, now):
    d = required(db, Device, b.device_id) if b.device_id else None
    if d and p.site_id and d.wisp_site_id != p.site_id:
        link = required(db, NetworkLink, p.link_id) if p.link_id else None
        if not link or (d.id, d.wisp_site_id) not in {
            (link.a_device_id, link.a_site_id),
            (link.z_device_id, link.z_site_id),
        }:
            raise ValueError("Equipment does not belong to prefix tower/link endpoint")
    if b.interface_id:
        i = required(db, Interface, b.interface_id)
        if (
            i.vrf_id != p.vrf_id
            or i.device_id != b.device_id
            or (i.vlan_id and i.vlan_id != p.vlan_id)
        ):
            raise ValueError("Interface, VLAN or routing context mismatch")
    if b.service_id:
        s = required(db, CustomerService, b.service_id)
        c = required(db, CustomerAccount, s.customer_id)
        if (
            s.status != "active"
            or s.source_deleted_at
            or c.source_deleted_at
            or s.mapping_state != "verified"
            or not timedelta(0) <= now - s.last_synced_at <= timedelta(hours=48)
        ):
            raise ValueError("Fresh reviewed active customer mapping required")
        sd = required(db, Device, s.device_id) if s.device_id else None
        sector = required(db, Sector, s.sector_id) if s.sector_id else None
        if (
            not s.site_id
            or (p.site_id and s.site_id != p.site_id)
            or (
                d
                and s.device_id != d.id
                and not (
                    d.external_id == s.equipment_id
                    and d.parent_device_id == s.device_id
                    and d.wisp_site_id == s.site_id
                )
            )
            or (sd and sd.wisp_site_id != s.site_id)
            or (
                sector
                and (sector.site_id != s.site_id or sector.device_id != s.device_id)
            )
        ):
            raise ValueError("Customer mapping is inconsistent with infrastructure")
    if b.pool_id:
        pool = required(db, Pool, b.pool_id)
        if pool.prefix_id != p.id:
            raise ValueError("Address pool context mismatch")


def occupied(db, p, now=None):
    # Even stale observations block automatic suggestions; absence never proves unused.
    assignments = list(
        db.scalars(
            select(Assignment.address)
            .where(
                Assignment.vrf_id == p.vrf_id,
                Assignment.state.in_(ACTIVE),
                Assignment.address.op("<<=")(p.network),
            )
            .limit(20001)
        )
    )
    observed = list(
        db.scalars(
            select(Observation.address)
            .where(
                Observation.vrf_id == p.vrf_id,
                Observation.address.is_not(None),
                Observation.address.op("<<=")(p.network),
            )
            .distinct()
            .limit(20001)
        )
    )
    if len(assignments) > 20000 or len(observed) > 20000:
        raise ValueError(
            "Narrow allocation scope; maximum20000 addresses per transaction"
        )
    return {int(ip_address(str(v))) for v in assignments + observed}


def select_address(db, p, b, now, ignore=None):
    if p.kind != "subnet":
        raise ValueError("Allocate addresses only in a documented leaf subnet")
    lo, hi = bounds(p.network)
    if b.pool_id:
        pool = required(db, Pool, b.pool_id)
        lo, hi = int(ip_address(str(pool.first))), int(ip_address(str(pool.last)))
    pools = list(db.scalars(select(Pool).where(Pool.prefix_id == p.id).limit(501)))
    if len(pools) > 500:
        raise ValueError("Narrow pool allocation scope")
    if b.address:
        a = ip_address(b.address)
        if a.version != ip_network(str(p.network)).version or not lo <= int(a) <= hi:
            raise ValueError("Address is not usable in this prefix/pool")
        if not b.pool_id and any(
            int(ip_address(str(pool.first)))
            <= int(a)
            <= int(ip_address(str(pool.last)))
            for pool in pools
        ):
            raise ValueError(
                "Select the documented pool explicitly before allocating inside its range"
            )
    else:
        used = occupied(db, p, now)
        candidate = lo
        if p.gateway:
            used.add(int(ip_address(str(p.gateway))))
        ranges = [(v, v) for v in used]
        if not b.pool_id:
            ranges.extend(
                (int(ip_address(str(pool.first))), int(ip_address(str(pool.last))))
                for pool in pools
            )
            recorded = list(
                db.scalars(
                    select(Observation)
                    .where(Observation.vrf_id == p.vrf_id, Observation.kind == "pools")
                    .order_by(Observation.id.desc())
                    .limit(501)
                )
            )
            if len(recorded) > 500:
                raise ValueError("Review/narrow observed pool scope")
            for observation in recorded:
                for value in observation.evidence.get("ranges", "").split(","):
                    try:
                        pair = value.split("-")
                        first, last = ip_address(pair[0]), ip_address(pair[-1])
                        if first.version == ip_network(str(p.network)).version:
                            ranges.append((int(first), int(last)))
                    except ValueError:
                        continue
        for first, last in sorted(ranges):
            if last < candidate:
                continue
            if first > candidate:
                break
            candidate = last + 1
        if candidate > hi:
            raise ValueError(
                "No documented candidate capacity; observations are not proof of availability"
            )
        a = (IPv4Address if ip_network(str(p.network)).version == 4 else IPv6Address)(
            candidate
        )
    dupe = db.scalar(
        select(Assignment.id).where(
            Assignment.vrf_id == p.vrf_id,
            Assignment.address == str(a),
            Assignment.state.in_(ACTIVE),
            Assignment.id != (ignore or -1),
        )
    )
    if dupe:
        raise ValueError("Address already assigned/reserved in this routing domain")
    if p.gateway and str(a) == str(p.gateway) and b.kind != "gateway":
        raise ValueError("Gateway is withheld from ordinary allocations")
    return str(a)


def allocate(db, b, actor, *, update=None):
    lock(db)
    p = required(db, Prefix, b.prefix_id)
    now = clock()
    association(db, p, b, now)
    address = select_address(db, p, b, now, update.id if update else None)
    data = b.model_dump(exclude={"reason", "address"})
    checked_text(data)
    if update:
        if (
            update.state == "released"
            or update.prefix_id != p.id
            or str(update.address) != address
        ):
            raise ValueError(
                "Released/context-changing assignments require a new reviewed allocation"
            )
        before = view(update)
        for k, v in data.items():
            setattr(update, k, v)
        update.updated_at = now
        row = update
    else:
        before = {}
        row = Assignment(**data, address=address, vrf_id=p.vrf_id, actor_id=actor)
        db.add(row)
    db.flush()
    record(
        db,
        actor,
        row,
        "assignment_updated" if update else "assignment_created",
        b.reason,
        before,
    )
    if b.dns_name and not db.scalar(
        select(DNSRecord.id).where(
            DNSRecord.assignment_id == row.id, DNSRecord.name == b.dns_name
        )
    ):
        db.add(
            DNSRecord(
                assignment_id=row.id,
                name=b.dns_name,
                record_type="A" if ip_address(address).version == 4 else "AAAA",
                value=address,
            )
        )
    return row


def release(db, row, reason, actor):
    lock(db)
    if row.state == "released":
        raise ValueError("Assignment already released")
    before = view(row)
    row.state = "released"
    row.released_at = row.updated_at = clock()
    record(db, actor, row, "assignment_released", reason, before)
    return row


def utilization(db, p):
    n = ip_network(str(p.network))
    lo, hi = bounds(n)
    total = max(0, hi - lo + 1)
    if p.kind == "block":
        children = list(
            db.scalars(
                select(Prefix.network).where(Prefix.parent_id == p.id).limit(5001)
            )
        )
        used = sum(ip_network(str(v)).num_addresses for v in children)
        return {
            "basis": "documented child-prefix allocation",
            "total": str(n.num_addresses),
            "used": str(used),
            "available": str(n.num_addresses - used),
            "percent": 100 * used / n.num_addresses,
        }
    rows = db.execute(
        select(Assignment.state, func.count())
        .where(Assignment.prefix_id == p.id, Assignment.state.in_(ACTIVE))
        .group_by(Assignment.state)
    ).all()
    counts = dict(rows)
    observed = (
        db.scalar(
            select(func.count(func.distinct(Observation.address))).where(
                Observation.vrf_id == p.vrf_id, Observation.address.op("<<=")(p.network)
            )
        )
        or 0
    )
    used = sum(counts.values())
    gateway_unassigned = int(
        bool(
            p.gateway
            and not db.scalar(
                select(Assignment.id).where(
                    Assignment.vrf_id == p.vrf_id,
                    Assignment.address == p.gateway,
                    Assignment.state.in_(ACTIVE),
                )
            )
        )
    )
    return {
        "basis": "documented host allocation; not reachability",
        "total": str(total),
        "used": str(used),
        "assigned": counts.get("assigned", 0),
        "reserved": counts.get("reserved", 0),
        "gateway_withheld": gateway_unassigned,
        "available": str(max(0, total - used - gateway_unassigned)),
        "observed_distinct": observed,
        "percent": 100 * (used + gateway_unassigned) / total if total else 0,
        "observations_do_not_prove_free": True,
    }


def suggest_prefixes(db, b):
    p = required(db, Prefix, b.prefix_id)
    net = ip_network(str(p.network))
    if p.kind != "block" or not net.prefixlen < b.length <= net.max_prefixlen:
        raise ValueError("Choose a more specific prefix inside a block")
    size = 1 << (net.max_prefixlen - b.length)
    candidate = int(net.network_address)
    children = list(
        db.scalars(
            select(Prefix.network)
            .where(Prefix.parent_id == p.id)
            .order_by(Prefix.network)
            .limit(5001)
        )
    )
    if len(children) > 5000:
        raise ValueError("Narrow planning scope")
    ranges = sorted(
        (
            int(ip_network(str(c)).network_address),
            int(ip_network(str(c)).broadcast_address),
        )
        for c in children
    )
    result = []
    while candidate + size - 1 <= int(net.broadcast_address) and len(result) < b.count:
        conflict = next(
            ((a, z) for a, z in ranges if a <= candidate + size - 1 and z >= candidate),
            None,
        )
        if conflict:
            candidate = ((conflict[1] + 1 + size - 1) // size) * size
            continue
        result.append(
            str(
                ip_network(
                    f"{(IPv4Address if net.version==4 else IPv6Address)(candidate)}/{b.length}"
                )
            )
        )
        candidate += size
    return {
        "candidates": result,
        "evidence": {"parent_id": p.id, "documented_child_prefixes": len(children)},
        "confidence": "documentation_only",
        "requires_operator_approval": True,
        "equipment_changes": 0,
        "caution": "Candidates exclude documented child prefixes, not undocumented live use; validate observations and routing before any future deployment",
    }


def reconcile(db, vrf_id, *, limit=500):
    now = clock()
    rows = list(
        db.scalars(
            select(Observation)
            .where(Observation.vrf_id == vrf_id)
            .order_by(Observation.observed_at.desc(), Observation.id.desc())
            .limit(limit)
        )
    )
    assignments = list(
        db.scalars(
            select(Assignment)
            .where(Assignment.vrf_id == vrf_id, Assignment.state.in_(ACTIVE))
            .limit(20001)
        )
    )
    if len(assignments) > 20000:
        raise ValueError("Narrow reconciliation scope")
    documented = {str(a.address): a for a in assignments}
    groups = {}
    for o in rows:
        if o.address:
            groups.setdefault(str(o.address), []).append(o)
    items = []
    for address, observations in groups.items():
        fresh = [
            o
            for o in observations
            if timedelta(0) <= now - o.observed_at <= timedelta(minutes=15)
        ]
        # Only simultaneous authoritative interface-address claims on distinct devices are potential conflicts.
        claims = {
            o.device_id
            for o in fresh
            if o.kind == "interface_address" and not ip_address(address).is_link_local
        }
        a = documented.get(address)
        conflict = len(claims) > 1 or bool(a and a.device_id and claims - {a.device_id})
        items.append(
            {
                "address": address,
                "classification": (
                    "potential_conflict"
                    if conflict
                    else a.state if a else "observed_unverified"
                ),
                "authoritative_assignment_id": a.id if a else None,
                "observed_devices": sorted(
                    {o.device_id for o in observations if o.device_id}
                ),
                "fresh_observations": len(fresh),
                "stale": not fresh,
                "ownership": "documented" if a else "unknown",
                "evidence_ids": [o.id for o in observations[:10]],
                "confidence": (
                    "suspected_inconsistency" if conflict else "recorded_observation"
                ),
                "equipment_changes": 0,
            }
        )
    return {
        "items": items,
        "observations_examined": len(rows),
        "bounded": len(rows) == limit,
        "absent_observations_mean_free": False,
        "confirmed_duplicate_assignments": 0,
    }


def recommendations(db, p):
    u = utilization(db, p)
    items = []
    now = clock()

    def add(
        rule, evidence, action, priority="medium", confidence="documented_evidence"
    ):
        items.append(
            {
                "rule": rule,
                "evidence": evidence,
                "action": action,
                "priority": priority,
                "confidence": confidence,
                "approval_required": True,
                "documentation_only": True,
            }
        )

    if u["percent"] >= 85 and (
        p.kind == "block" or ip_network(str(p.network)).num_addresses > 2
    ):
        add(
            "address_exhaustion",
            u,
            "Review documented utilization and prepare an approved expansion plan",
            "high",
        )
    old = db.scalar(
        select(func.count(Assignment.id)).where(
            Assignment.prefix_id == p.id,
            Assignment.state == "reserved",
            Assignment.created_at < now - timedelta(days=30),
        )
    )
    if old:
        add(
            "aged_reservations",
            {"count": old},
            "Review reservation owners; age or absent telemetry does not authorize release",
        )
    rs = reconcile(db, p.vrf_id)
    conflicts = [
        r
        for r in rs["items"]
        if ip_address(r["address"]) in ip_network(str(p.network))
        and r["classification"] == "potential_conflict"
    ]
    if conflicts:
        add(
            "potential_address_conflict",
            {"observations": conflicts[:20]},
            "Verify routing context, shared/virtual addresses and documentation; one observation is not a configuration fault",
            "high",
            "suspected_inconsistency",
        )
    unknown = [
        r
        for r in rs["items"]
        if ip_address(r["address"]) in ip_network(str(p.network))
        and r["ownership"] == "unknown"
    ]
    if unknown:
        add(
            "unverified_infrastructure",
            {"count_in_bounded_sample": len(unknown)},
            "Review source and ownership before allocating; do not assume a stale/absent address is free",
        )
    if p.link_id and not db.scalar(
        select(Assignment.id).where(
            Assignment.prefix_id == p.id,
            Assignment.kind == "point_to_point",
            Assignment.state.in_(ACTIVE),
        )
    ):
        add(
            "point_to_point_documentation",
            {"link_id": p.link_id},
            "Review both registered endpoints and document their VRF/interface addresses",
        )
    if p.kind == "subnet":
        values = sorted(
            int(ip_address(str(v)))
            for v in db.scalars(
                select(Assignment.address)
                .where(Assignment.prefix_id == p.id, Assignment.state.in_(ACTIVE))
                .limit(20001)
            )
        )
        if len(values) > 20000:
            raise ValueError("Narrow analysis scope")
        gaps = sum(b - a > 1 for a, b in zip(values, values[1:]))
        if gaps >= 3:
            add(
                "fragmented_documented_space",
                {"internal_gaps": gaps},
                "Review contiguous planning needs; no renumbering or automatic release",
            )
    observed = list(
        db.scalars(
            select(Observation)
            .where(
                Observation.vrf_id == p.vrf_id,
                Observation.kind.in_(["route", "interface_address", "vlan"]),
                Observation.observed_at >= now - timedelta(minutes=15),
            )
            .order_by(Observation.id.desc())
            .limit(500)
        )
    )
    observed_vlans = {
        o.evidence.get("name"): o.evidence.get("vlan_id")
        for o in observed
        if o.kind == "vlan"
    }
    for o in observed:
        if o.network and ip_network(str(o.network)) == ip_network(str(p.network)):
            observed_vid = observed_vlans.get(o.evidence.get("interface"))
            if (
                p.vlan_id
                and observed_vid
                and required(db, VLAN, p.vlan_id).vid != observed_vid
            ):
                add(
                    "vlan_interface_documentation_differs",
                    {"observation_id": o.id, "observed_vid": observed_vid},
                    "Review observed VLAN interface and documented subnet; do not change tagging automatically",
                    confidence="suspected_inconsistency",
                )
            if (
                o.evidence.get("gateway")
                and p.gateway
                and o.evidence["gateway"] != str(p.gateway)
            ):
                add(
                    "gateway_documentation_differs",
                    {"observation_id": o.id, "documented_gateway": str(p.gateway)},
                    "Verify route next-hop semantics; this difference alone is not a router configuration error",
                    confidence="suspected_inconsistency",
                )
            if o.evidence.get("vlan_id") and p.vlan_id:
                vlan = required(db, VLAN, p.vlan_id)
                if o.evidence["vlan_id"] != vlan.vid:
                    add(
                        "vlan_documentation_differs",
                        {"observation_id": o.id, "documented_vid": vlan.vid},
                        "Review tagged/untagged context and interface association",
                        confidence="suspected_inconsistency",
                    )
    return items


CSV_FIELDS = {
    "type",
    "vrf_id",
    "network",
    "parent_id",
    "site_id",
    "vlan_id",
    "link_id",
    "kind",
    "scope",
    "purpose",
    "owner",
    "gateway",
    "prefix_id",
    "address",
    "state",
    "device_id",
    "interface_id",
    "service_id",
    "pool_id",
    "dns_name",
}
INT_FIELDS = {
    "vrf_id",
    "parent_id",
    "site_id",
    "vlan_id",
    "link_id",
    "prefix_id",
    "device_id",
    "interface_id",
    "service_id",
    "pool_id",
}


def parse_csv(value, reason):
    reader = csv.DictReader(io.StringIO(value))
    headers = reader.fieldnames
    if not headers or len(headers) != len(set(headers)) or set(headers) - CSV_FIELDS:
        raise ValueError("Unknown/duplicate CSV columns; use the documented template")
    rows = []
    for row in reader:
        if len(rows) >= 500:
            raise ValueError("Maximum500 import rows")
        if None in row:
            raise ValueError("Malformed CSV row")
        data = {
            k: (int(v) if k in INT_FIELDS else v)
            for k, v in row.items()
            if v not in ("", None)
        }
        kind = data.pop("type", "assignment")
        data["reason"] = reason
        if kind not in {"assignment", "prefix"}:
            raise ValueError("Import type must be assignment or prefix")
        b = (PrefixInput if kind == "prefix" else AssignmentInput).model_validate(data)
        rows.append({"type": kind, "data": b.model_dump(mode="json")})
    if not rows:
        raise ValueError("Import contains no rows")
    return rows


def validate_import(db, rows, actor):
    errors = []
    # Execute the actual allocation validation inside a savepoint, then always roll it back.
    save = db.begin_nested()
    try:
        for i, row in enumerate(rows):
            try:
                with db.begin_nested():
                    (create_prefix if row["type"] == "prefix" else allocate)(
                        db,
                        (
                            PrefixInput if row["type"] == "prefix" else AssignmentInput
                        ).model_validate(row["data"]),
                        actor,
                    )
            except (ValueError, LookupError):
                errors.append(
                    {
                        "row": i + 1,
                        "error": "Invalid, duplicate or conflicting allocation; review context, mapping and current capacity",
                    }
                )
    finally:
        save.rollback()
    return errors


def preview_import(db, b, actor):
    lock(db)
    try:
        rows = parse_csv(b.csv_text, b.reason)
    except Exception:
        raise ValueError(
            "Invalid CSV schema or field values; no assignments imported"
        ) from None
    errors = validate_import(db, rows, actor)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    batch = ImportBatch(actor_id=actor, digest=digest, rows=rows, errors=errors)
    db.add(batch)
    db.flush()
    return {
        "id": batch.id,
        "digest": digest,
        "rows": len(rows),
        "errors": errors,
        "dry_run": True,
        "requires_approval": True,
    }


def apply_import(db, ident, b, actor):
    lock(db)
    batch = required(db, ImportBatch, ident)
    if (
        not b.approved
        or b.digest != batch.digest
        or batch.state != "preview"
        or batch.errors
    ):
        raise ValueError(
            "Explicit approval of an error-free unchanged preview required"
        )
    if clock() - batch.created_at > timedelta(hours=24):
        raise ValueError("Import preview expired; create a fresh preview")
    result = []
    for row in batch.rows:
        data = {**row["data"], "reason": b.reason}
        entry = (create_prefix if row["type"] == "prefix" else allocate)(
            db,
            (
                PrefixInput if row["type"] == "prefix" else AssignmentInput
            ).model_validate(data),
            actor,
        )
        result.append(entry.id)
    batch.rows = [
        {**row, "applied_record_id": ident} for row, ident in zip(batch.rows, result)
    ]
    batch.state = "applied"
    batch.applied_at = clock()
    return {"applied": len(result), "ids": result, "equipment_changes": 0}


def export_csv(db, vrf_id, *, customer_data=False):
    rows = list(
        db.scalars(
            select(Assignment)
            .where(Assignment.vrf_id == vrf_id)
            .order_by(Assignment.id)
            .limit(1001)
        )
    )
    if len(rows) > 1000:
        raise ValueError("Narrow export scope; maximum1000 rows")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "id",
            "vrf_id",
            "prefix_id",
            "address",
            "state",
            "kind",
            "device_id",
            "service_id",
            "owner",
        ]
    )
    for r in rows:
        if (r.service_id or r.kind == "customer") and not customer_data:
            continue
        vals = [
            r.id,
            r.vrf_id,
            r.prefix_id,
            str(r.address),
            r.state,
            r.kind,
            r.device_id,
            r.service_id if customer_data else "",
            r.owner,
        ]
        w.writerow(
            [
                (
                    ("'" + v)
                    if isinstance(v, str)
                    and v.lstrip().startswith(("=", "+", "-", "@"))
                    else v
                )
                for v in vals
            ]
        )
    return out.getvalue()


def utilization_many(db, prefixes):
    """Four bounded queries per prefix page, rather than one query per row."""
    from sqlalchemy import case, and_

    if not prefixes:
        return {}
    ids = [p.id for p in prefixes]
    counts = {}
    gateway_used = set()
    for ident, state, count, gateways in db.execute(
        select(
            Assignment.prefix_id,
            Assignment.state,
            func.count(),
            func.sum(case((Assignment.address == Prefix.gateway, 1), else_=0)),
        )
        .join(Prefix, Prefix.id == Assignment.prefix_id)
        .where(Assignment.prefix_id.in_(ids), Assignment.state.in_(ACTIVE))
        .group_by(Assignment.prefix_id, Assignment.state)
    ):
        counts.setdefault(ident, {})[state] = count
        if gateways:
            gateway_used.add(ident)
    observed = dict(
        db.execute(
            select(Prefix.id, func.count(func.distinct(Observation.address)))
            .join(
                Observation,
                and_(
                    Observation.vrf_id == Prefix.vrf_id,
                    Observation.address.op("<<=")(Prefix.network),
                ),
            )
            .where(Prefix.id.in_(ids), Prefix.kind == "subnet")
            .group_by(Prefix.id)
        ).all()
    )
    children = {}
    child_rows = list(
        db.execute(
            select(Prefix.parent_id, Prefix.network)
            .where(Prefix.parent_id.in_(ids))
            .limit(5001)
        )
    )
    if len(child_rows) > 5000:
        raise ValueError("Narrow prefix utilization scope")
    for ident, network in child_rows:
        children[ident] = (
            children.get(ident, 0) + ip_network(str(network)).num_addresses
        )
    result = {}
    for p in prefixes:
        net = ip_network(str(p.network))
        if p.kind == "block":
            used = children.get(p.id, 0)
            total = net.num_addresses
            result[p.id] = {
                "basis": "documented child-prefix allocation",
                "total": str(total),
                "used": str(used),
                "available": str(total - used),
                "percent": 100 * used / total,
            }
        else:
            lo, hi = bounds(net)
            total = max(0, hi - lo + 1)
            c = counts.get(p.id, {})
            used = sum(c.values())
            withheld = int(bool(p.gateway and p.id not in gateway_used))
            result[p.id] = {
                "basis": "documented host allocation; not reachability",
                "total": str(total),
                "used": str(used),
                "assigned": c.get("assigned", 0),
                "reserved": c.get("reserved", 0),
                "gateway_withheld": withheld,
                "available": str(max(0, total - used - withheld)),
                "observed_distinct": observed.get(p.id, 0),
                "percent": 100 * (used + withheld) / total if total else 0,
                "observations_do_not_prove_free": True,
            }
    return result
