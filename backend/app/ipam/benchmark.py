"""Synthetic stored IPAM workloads, not live discovery or production sizing."""

import json, time, resource
from ipaddress import ip_network, ip_address
from sqlalchemy import select, event, insert, func
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Site, Device, User
from .models import VRF, Prefix, Assignment, Observation
from . import service as s
from .contracts import Suggestion


def measure(db, count):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit isolated synthetic benchmark required")
    if db.scalar(select(VRF.id)):
        raise ValueError("Empty isolated IPAM fixture required")
    now = s.clock()
    actor = User(
        username="noc99-benchmark",
        email="noc99@example.invalid",
        password_hash="synthetic-unusable",
        role="operator",
        is_active=True,
    )
    db.add(actor)
    db.flush()
    vrfs = [VRF(name=f"Synthetic routing context {n}") for n in range(2)]
    sites = [Site(name=f"NOC99 Synthetic POP {i:02}") for i in range(40)]
    db.add_all(vrfs + sites)
    db.flush()
    devices = [
        Device(
            source="synthetic",
            external_id=f"noc99-scale-{i}",
            name=f"NOC99 Synthetic equipment {i}",
            wisp_site_id=sites[i % 40].id,
        )
        for i in range(400)
    ]
    db.add_all(devices)
    db.flush()
    parents = []
    for v in vrfs:
        for network in ["10.0.0.0/8", "2001:db8::/32"]:
            p = Prefix(vrf_id=v.id, network=network, kind="block")
            db.add(p)
            db.flush()
            parents.append(p)
    prefixes = []
    for i in range(1000):
        v = vrfs[i % 2]
        ipv6 = i % 4 in {2, 3}
        n = i // 4
        network = f"2001:db8:{n:x}::/64" if ipv6 else f"10.{n//256}.{n%256}.0/24"
        parent = next(
            p for p in parents if p.vrf_id == v.id and (":" in str(p.network)) == ipv6
        )
        prefixes.append(
            Prefix(
                vrf_id=v.id,
                network=network,
                parent_id=parent.id,
                site_id=sites[i % 40].id,
                kind="subnet",
                scope="private",
            )
        )
    db.add_all(prefixes)
    db.flush()
    records = []
    for i in range(count):
        p = prefixes[i % 1000]
        address = str(
            ip_address(int(ip_network(str(p.network)).network_address) + 1 + i // 1000)
        )
        records.append(
            {
                "vrf_id": p.vrf_id,
                "prefix_id": p.id,
                "address": address,
                "state": "assigned",
                "kind": "management",
                "owner": "Synthetic scale inventory",
                "actor_id": actor.id,
                "device_id": devices[i % 40].id,
            }
        )
    start = time.perf_counter()
    db.execute(insert(Assignment), records)
    db.flush()
    ingest = (time.perf_counter() - start) * 1000
    queries = []

    def measured(conn, cursor, statement, params, ctx, many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    conn = db.connection()
    event.listen(conn, "before_cursor_execute", measured)
    try:
        start = time.perf_counter()
        page = list(
            db.scalars(
                select(Assignment)
                .where(Assignment.vrf_id == vrfs[0].id)
                .order_by(Assignment.id)
                .limit(100)
            )
        )
        pagination = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        matches = list(
            db.scalars(
                select(Prefix).where(
                    Prefix.vrf_id == vrfs[0].id,
                    Prefix.network.op(">>=")(str(page[0].address)),
                )
            )
        )
        containment = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        prefix_page = list(db.scalars(select(Prefix).order_by(Prefix.id).limit(50)))
        prefix_usage = s.utilization_many(db, prefix_page)
        prefix_page_ms = (time.perf_counter() - start) * 1000
        assert len(prefix_usage) == 50
        start = time.perf_counter()
        u = s.utilization(db, prefixes[0])
        utilization = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        suggestion = s.suggest_prefixes(
            db, Suggestion(prefix_id=parents[0].id, length=24, count=4)
        )
        planning = (time.perf_counter() - start) * 1000
        assert (
            len(page) == 100 and len(matches) == 2 and int(u["used"]) == count // 1000
        )
        assert len(suggestion["candidates"]) == 4
        return {
            "sites": 40,
            "devices": 400,
            "routing_contexts": 2,
            "prefixes": 1004,
            "assignments": count,
            "insert_ms": round(ingest, 2),
            "page_100_ms": round(pagination, 2),
            "prefix_page_50_with_utilization_ms": round(prefix_page_ms, 2),
            "prefix_containment_ms": round(containment, 2),
            "utilization_ms": round(utilization, 2),
            "planning_ms": round(planning, 2),
            "selects": len(queries),
            "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "external_calls": 0,
            "limitations": "Synthetic stored records; not native discovery throughput, millions of dense observations or production sizing",
        }
    finally:
        event.remove(conn, "before_cursor_execute", measured)


if __name__ == "__main__":
    for count in [1000, 10000]:
        with SessionLocal() as db:
            try:
                print(json.dumps(measure(db, count)), flush=True)
            finally:
                db.rollback()
