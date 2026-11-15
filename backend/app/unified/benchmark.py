"""Rollback-only 40-POP synthetic network; no sockets/devices/discovery."""

import json, time, resource
from sqlalchemy import select, insert, event
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Site, Device, User
from app.customers.models import CustomerAccount, CustomerService
from .models import *
from . import service as s


def measure(strand_count, customers):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit isolated offline staging required")
    with SessionLocal() as db:
        if db.scalar(select(Node.id)):
            raise ValueError("Empty isolated unified graph required")
        u = User(
            username="unified-scale",
            email="unified-scale@example.invalid",
            password_hash="synthetic-unusable",
            role="admin",
            is_active=True,
        )
        db.add(u)
        sites = [
            Site(
                name=f"NOC104 Synthetic POP {i}",
                latitude=41.4 + i * 0.001,
                longitude=-84.2 + i * 0.001,
            )
            for i in range(40)
        ]
        db.add_all(sites)
        db.flush()
        ns = [
            Node(site_id=t.id, kind="core" if i == 0 else "pop")
            for i, t in enumerate(sites)
        ]
        db.add_all(ns)
        db.flush()
        devices = [
            Device(
                name=f"Synthetic infrastructure {i}",
                source="noc104-synthetic",
                external_id=str(i),
                wisp_site_id=sites[i % 40].id,
            )
            for i in range(400)
        ]
        db.add_all(devices)
        db.flush()
        db.add_all([Node(device_id=d.id, kind="equipment") for d in devices])
        account = CustomerAccount(
            external_id="NOC104-SYN-C", source_updated_at=now(), last_synced_at=now()
        )
        db.add(account)
        db.flush()
        services = [
            CustomerService(
                external_id=f"NOC104-SYN-S{i}",
                customer_id=account.id,
                status="active",
                service_type="hybrid",
                site_id=sites[i % 39 + 1].id,
                source_updated_at=now(),
                last_synced_at=now(),
            )
            for i in range(customers)
        ]
        db.add_all(services)
        db.flush()
        cnodes = [Node(service_id=t.id, kind="customer") for t in services]
        db.add_all(cnodes)
        db.flush()
        c = [
            Circuit(
                code=f"NOC104-C{i}",
                service_id=t.id,
                purpose="business",
                state="active",
                topology_complete=True,
                completeness_evidence="Synthetic complete dual path fixture",
            )
            for i, t in enumerate(services)
        ]
        db.add_all(c)
        db.flush()
        fiber = [
            Edge(
                label=f"NOC104 Fiber {i}",
                a_id=ns[0].id,
                z_id=ns[i % 39 + 1].id,
                layer="physical",
                relation="transport",
                technology="fiber",
                state="installed",
                evidence="Synthetic route",
            )
            for i in range(40)
        ]
        wireless = [
            Edge(
                label=f"NOC104 Microwave {i}",
                a_id=ns[0].id,
                z_id=ns[i + 1].id,
                layer="physical",
                relation="transport",
                technology="microwave",
                state="installed",
                evidence="Synthetic backup",
            )
            for i in range(39)
        ]
        access = [
            Edge(
                label=f"NOC104 Access {i}",
                a_id=ns[i % 39 + 1].id,
                z_id=n.id,
                layer="physical",
                relation="access",
                technology="wireless",
                state="installed",
                evidence="Synthetic access",
            )
            for i, n in enumerate(cnodes)
        ]
        db.add_all(fiber + wireless + access)
        db.flush()
        cables = [
            Cable(
                code=f"NOC104 F{i}",
                edge_id=e.id,
                strand_count=strand_count // 40,
                geometry=[[-84.2, 41.4], [-84.19, 41.41]],
            )
            for i, e in enumerate(fiber)
        ]
        db.add_all(cables)
        db.flush()
        begin = time.perf_counter()
        strands = []
        # Each site route supplies its assigned synthetic customers; spare count remains explicit.
        for i, cable in enumerate(cables):
            customers_for = (
                [j for j in range(customers) if j % 39 == i] if i < 39 else []
            )
            for number in range(cable.strand_count):
                ci = c[customers_for[number]] if number < len(customers_for) else None
                strands.append(
                    Strand(
                        cable_id=cable.id,
                        number=number + 1,
                        state="assigned" if ci else "available",
                        circuit_id=ci.id if ci else None,
                    )
                )
        db.add_all(strands)
        db.flush()
        byc = {r.circuit_id: r for r in strands if r.circuit_id}
        db.add_all(
            [
                End(strand_id=r.id, side=side, node_id=n)
                for r in strands
                for side, n in [
                    ("a", ns[0].id),
                    (
                        "z",
                        ns[
                            cables.index(
                                next(ca for ca in cables if ca.id == r.cable_id)
                            )
                            % 39
                            + 1
                        ].id,
                    ),
                ]
            ]
        )
        paths = []
        for i, ci in enumerate(c):
            for name in ["primary", "backup"]:
                paths.append(
                    Path(
                        circuit_id=ci.id,
                        name=name,
                        state="active",
                        source_id=ns[0].id,
                        destination_id=cnodes[i].id,
                        verified_at=now(),
                        verified_until=now()
                        + __import__("datetime").timedelta(minutes=10),
                        verification_evidence="Synthetic end-to-end verification fixture",
                    )
                )
        nm = {n.id: n for n in ns + cnodes}
        em = {e.id: e for e in fiber + wireless + access}
        sm = {s.id: s for s in services}
        for i in range(customers):
            for k, p in enumerate(paths[2 * i : 2 * i + 2]):
                p.inventory_snapshot = s.dependency_values(
                    nm,
                    em,
                    {},
                    {},
                    sm,
                    {ns[0].id, ns[i % 39 + 1].id, cnodes[i].id},
                    {fiber[i % 39].id if k == 0 else wireless[i % 39].id, access[i].id},
                )
        db.add_all(paths)
        db.flush()
        for i in range(customers):
            for k, p in enumerate(paths[2 * i : 2 * i + 2]):
                db.add(
                    Hop(
                        path_id=p.id,
                        position=0,
                        edge_id=(fiber[i % 39].id if k == 0 else wireless[i % 39].id),
                        strand_id=byc[c[i].id].id if k == 0 else None,
                    )
                )
                db.add(Hop(path_id=p.id, position=1, edge_id=access[i].id))
        db.flush()
        insert_ms = (time.perf_counter() - begin) * 1000
        queries = []

        def before(*args):
            queries.append(1)

        event.listen(db.get_bind(), "before_cursor_execute", before)
        try:
            begin = time.perf_counter()
            graph = s.graph_view(db)
            map_ms = (time.perf_counter() - begin) * 1000
            begin = time.perf_counter()
            impact = s.impact(db, [], [fiber[0].id])
            impact_ms = (time.perf_counter() - begin) * 1000
            assert (
                impact["unique_customer_count"] == 0
                and len(impact["preserved_services"]) == customers
            )
            begin = time.perf_counter()
            trace = s.trace_strand(db, strands[0].id)
            trace_ms = (time.perf_counter() - begin) * 1000
            return {
                "pops": 40,
                "infrastructure_devices": 400,
                "customer_services": customers,
                "fiber_strands": strand_count,
                "paths": 2 * customers,
                "insert_ms": round(insert_ms, 2),
                "map_page_ms": round(map_ms, 2),
                "impact_ms": round(impact_ms, 2),
                "strand_trace_ms": round(trace_ms, 2),
                "read_statements": len(queries),
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "external_calls": 0,
                "scope": "Single-process stored PostgreSQL graph, not optical/RF validation, HTTP concurrency or production sizing",
            }
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", before)
            db.rollback()


if __name__ == "__main__":
    for args in [(1000, 500), (10000, 1000)]:
        print(json.dumps(measure(*args)), flush=True)
