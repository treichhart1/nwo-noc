"""Exact allowlisted RouterOS print/read contracts. No scan or configuration channel."""

import hashlib, json, re
from datetime import timedelta
from ipaddress import ip_address, ip_network, ip_interface
from sqlalchemy import select, text
from app.core.config import settings
from app.models.entities import Device, TelemetrySample
from app.integrations.read_only import (
    ReadPolicy,
    ReadClient,
    IntegrationFailure,
    secret,
)
from .models import VRF, Observation, DiscoveryBatch
from .service import lock, clock, required, checked_text

PATHS = {
    "addresses": "/ip/address",
    "ipv6_addresses": "/ipv6/address",
    "interfaces": "/interface",
    "vlans": "/interface/vlan",
    "bridges": "/interface/bridge",
    "tables": "/routing/table",
    "routes": "/ip/route",
    "ipv6_routes": "/ipv6/route",
    "ospf": "/routing/ospf/neighbor",
    "dhcp_networks": "/ip/dhcp-server/network",
    "pools": "/ip/pool",
    "leases": "/ip/dhcp-server/lease",
    "arp": "/ip/arp",
    "neighbors": "/ipv6/neighbor",
    "identity": "/system/identity",
}
REST_PATHS = frozenset("/rest" + p for p in PATHS.values())
API6 = frozenset(p + "/print" for k, p in PATHS.items() if k != "tables")
API7 = frozenset(p + "/print" for p in PATHS.values())
SAFE_FIELDS = {
    "interface",
    "name",
    "type",
    "actual-interface",
    "routing-table",
    "routing-mark",
    "vrf",
    "bridge",
    "state",
    "status",
    "dynamic",
    "disabled",
    "vlan-id",
    "gateway",
    "distance",
    "active",
    "router-id",
    "address",
    "dst-address",
    "network",
    "ranges",
    "mac-address",
    "host-name",
    "server",
    "expires-after",
    ".id",
    "default-route-distance",
}


def scalar(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (str, int)) and len(str(v)) <= 253:
        value = str(v)
        if any(ord(c) < 32 for c in value):
            raise ValueError("Malformed observation")
        checked_text({"value": value})
        return value
    raise ValueError("Malformed bounded scalar")


def normalize(b):
    allowed = (
        REST_PATHS
        if b.protocol == "rest7"
        else API6 if b.protocol == "api6_recorded" else API7
    )
    if set(b.responses) - allowed:
        raise ValueError("Only allowlisted read-only print/GET resources accepted")
    rows = []
    unsupported = []
    for path, payload in b.responses.items():
        base = path.removeprefix("/rest").removesuffix("/print")
        kind = next(k for k, v in PATHS.items() if v == base)
        if payload is None:
            unsupported.append(kind)
            continue
        if isinstance(payload, dict) and kind == "identity":
            payload = [payload]
        if not isinstance(payload, list) or len(payload) > 2000:
            raise ValueError("Malformed or oversized resource response")
        for raw in payload:
            if not isinstance(raw, dict):
                raise ValueError("Malformed resource row")
            # Never preserve arbitrary RouterOS fields, lease comments, tokens or passwords.
            data = {
                k: scalar(v)
                for k, v in raw.items()
                if k in SAFE_FIELDS and v is not None
            }
            address = network = None
            typ = kind
            try:
                if kind in {"addresses", "ipv6_addresses"}:
                    v = ip_interface(data["address"])
                    address = str(v.ip)
                    network = str(v.network)
                    typ = "interface_address"
                    if data.get("disabled") in {True, "true", "yes"}:
                        typ = "disabled_address"
                elif kind in {"arp", "neighbors", "leases"}:
                    address = str(ip_address(data["address"]))
                    if "%" in address:
                        raise ValueError()
                    typ = {
                        "arp": "arp",
                        "neighbors": "neighbor",
                        "leases": "dhcp_lease",
                    }[kind]
                elif kind in {"routes", "ipv6_routes", "dhcp_networks"}:
                    network = str(
                        ip_network(
                            (
                                data["dst-address"]
                                if kind != "dhcp_networks"
                                else data["address"]
                            ),
                            strict=True,
                        )
                    )
                    typ = "route" if kind != "dhcp_networks" else "dhcp_network"
                elif kind == "vlans":
                    vid = int(data["vlan-id"])
                    if not 1 <= vid <= 4094:
                        raise ValueError()
                    data["vlan_id"] = vid
                    typ = "vlan"
                elif kind == "ospf":
                    # Router IDs are identifiers, not ownership claims.
                    if data.get("router-id"):
                        data["router-id"] = str(ip_address(data["router-id"]))
                elif kind == "pools":
                    ranges = data.get("ranges", "").split(",")
                    if len(ranges) > 32:
                        raise ValueError()
                    for r in ranges:
                        pair = r.split("-")
                        a = ip_address(pair[0])
                        z = ip_address(pair[-1])
                        if len(pair) > 2 or a.version != z.version or int(a) > int(z):
                            raise ValueError()
                if "gateway" in data:
                    # RouterOS next hops can be lists, recursive targets or interface-qualified.
                    # Preserve safe text, interpret only a single literal address.
                    try:
                        data["gateway"] = str(ip_address(data["gateway"]))
                    except ValueError:
                        data["gateway_uninterpreted"] = data.pop("gateway")
                if address and ip_address(address).is_multicast:
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise ValueError(
                    "Malformed address/resource observation; batch not imported"
                ) from None
            rows.append(
                {"kind": typ, "address": address, "network": network, "evidence": data}
            )
            if len(rows) > 5000:
                raise ValueError("Maximum5000 observations per reviewed batch")
    return rows, unsupported


def ingest(db, b, actor):
    lock(db)
    required(db, VRF, b.vrf_id)
    device = required(db, Device, b.device_id)
    if clock() - b.observed_at > timedelta(days=30):
        raise ValueError("Recorded discovery is outside30-day import window")
    # VRF/table names on RouterOS do not automatically establish NOC routing-domain identity.
    rows, unsupported = normalize(b)
    domains = {
        str(
            r["evidence"].get("routing-table")
            or r["evidence"].get("routing-mark")
            or r["evidence"].get("vrf")
            or "main"
        )
        for r in rows
        if r["kind"] in {"interface_address", "route"}
    }
    if len(domains) > 1:
        raise ValueError(
            "Split multi-routing-domain responses into separately reviewed batches"
        )
    # Hash only closed, credential-free evidence. Canonical rows make reorder-only
    # retransmissions idempotent and collapse exact duplicate resource rows.
    unique = {json.dumps(row, sort_keys=True): row for row in rows}
    rows = [unique[k] for k in sorted(unique)]
    canonical = {
        "device_id": b.device_id,
        "vrf_id": b.vrf_id,
        "protocol": b.protocol,
        "version": b.routeros_version,
        "observed_at": b.observed_at.isoformat(),
        "rows": rows,
        "unsupported": sorted(unsupported),
    }
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    old = db.get(DiscoveryBatch, b.batch_key)
    if old:
        if old.digest != digest:
            raise ValueError(
                "Discovery event key conflicts with prior immutable evidence"
            )
        return {"duplicate": True, "observations": old.count, "equipment_changes": 0}
    for i, row in enumerate(rows):
        db.add(
            Observation(
                batch_key=b.batch_key,
                ordinal=i,
                vrf_id=b.vrf_id,
                device_id=device.id,
                source=b.protocol,
                observed_at=b.observed_at,
                **row
            )
        )
    db.add(DiscoveryBatch(key=b.batch_key, digest=digest, count=len(rows)))
    from app.models.entities import AuditLog

    db.add(
        AuditLog(
            user_id=actor,
            action="ipam.recorded_discovery",
            entity_type="device",
            entity_id=str(device.id),
            details={
                "read_only": True,
                "observations": len(rows),
                "unsupported_resources": unsupported,
            },
        )
    )
    db.flush()
    return {
        "observations": len(rows),
        "unsupported": unsupported,
        "status": "partial" if unsupported else "recorded",
        "duplicate": False,
        "equipment_changes": 0,
        "routing_context_requires_review": True,
    }


def inventory_preview(db, vrf_id, *, limit=200):
    required(db, VRF, vrf_id)
    rows = list(
        db.scalars(
            select(Device)
            .where(Device.ip_address.is_not(None))
            .order_by(Device.id)
            .limit(limit)
        )
    )
    result = []
    for d in rows:
        try:
            a = str(ip_address(d.ip_address))
        except ValueError:
            continue
        result.append(
            {
                "device_id": d.id,
                "site_id": d.wisp_site_id,
                "address": a,
                "classification": "observed_unverified",
                "source": "inventory_documentation",
                "authoritative_assignment": False,
            }
        )
    return {
        "items": result,
        "bounded": len(rows) == limit,
        "equipment_changes": 0,
        "requires_explicit_allocation": True,
    }


def collect(db, device_id, vrf_id, actor, *, transport=None):
    # No route invokes this automatically; an administrator must enable both gates.
    if (
        settings.offline_mode
        or not settings.ipam_discovery_enabled
        or not settings.mikrotik_enabled
        or not settings.integrations_admin_approved
    ):
        raise IntegrationFailure("disabled")
    if device_id != settings.mikrotik_device_id:
        raise IntegrationFailure("endpoint_denied")
    required(db, Device, device_id)
    required(db, VRF, vrf_id)
    if not db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": 990000000 + device_id}
    ):
        raise IntegrationFailure("overlap_skipped")
    latest = db.scalar(
        select(Observation.observed_at)
        .where(Observation.device_id == device_id, Observation.source == "rest7")
        .order_by(Observation.observed_at.desc())
        .limit(1)
    )
    if latest and clock() - latest < timedelta(seconds=300):
        raise IntegrationFailure("rate_limited")
    from app.integrations.mikrotik import require_routeros7

    policy = ReadPolicy(
        settings.mikrotik_base_url,
        tuple(settings.mikrotik_allowed_origins.split(",")),
        REST_PATHS | frozenset(["/rest/system/resource"]),
        True,
        True,
        max_items=5000,
    )
    responses = {}
    with ReadClient(
        policy,
        auth=(settings.mikrotik_username, secret(settings.mikrotik_password)),
        transport=transport,
    ) as client:
        version = require_routeros7(client.request("/rest/system/resource")[0])[
            "version"
        ]
        for path in sorted(REST_PATHS):
            try:
                responses[path] = client.request(path)[0]
            except IntegrationFailure as exc:
                if exc.code != "unsupported":
                    raise
                responses[path] = None
    from .contracts import DiscoveryInput

    return ingest(
        db,
        DiscoveryInput(
            batch_key="rest7:" + str(device_id) + ":" + str(int(clock().timestamp())),
            vrf_id=vrf_id,
            device_id=device_id,
            observed_at=clock(),
            routeros_version=version,
            protocol="rest7",
            responses=responses,
            reason="Explicit administrator read-only IPAM collection",
        ),
        actor,
    )
