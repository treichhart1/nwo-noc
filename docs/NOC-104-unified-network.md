# NOC-104: unified wireless, fiber and hybrid network

## Dependency strategy

Branch `feature/noc-104-unified-network` is based directly on **unmerged NOC-103 draft PR #107**, commit `973351c29283661c4d51c84671cd9e755cc6be95`, whose CI #536 passed. The complete stack is preserved by ancestry: earlier NOC-83–94/RF dependencies → NOC-95 #99 → NOC-96 #100 → NOC-97 #101 → NOC-98 #102 → NOC-99 #103 → NOC-100 #104 → NOC-101 #105 → NOC-102 #106 → NOC-103 #107 → NOC-104. Review/integrate oldest first, and revalidate descendants after any upstream change. No PR is merged, closed or deployed here. Application version remains3.2.1-rc.3; database head becomes0023_unified_network, parent0022_customer_signup.

## One inventory, two topology layers

`unified_nodes` are graph annotations over exactly one existing Site, Asset, Device, Sector or Powercode CustomerService identity. They are not a second tower/equipment/customer inventory. UUID identifiers remain stable across renaming. A monitored device cannot be independently represented through both its Device and an Asset already linked to it. Equipment models, manufacturers, firmware, management addresses, locations and lifecycle remain in NOC-100/the existing inventory. Do not copy credentials into graph labels or reasons.

Kinds cover core/headend/upstream sites, towers/POPs, sectors, radios, cabinets, splice enclosures, patch panels/ODFs, OLTs, splitters, ONTs, customer premises and hybrid interconnections. A site kind uses an existing Site; a serialized component uses an existing Asset or monitored Device; a customer endpoint uses the existing service identifier. Exact customer coordinates are not invented: the map explicitly labels serving-site anchors and unavailable exact locations.

Edges explicitly separate **physical** transport/access from **logical** dependencies. Physical technologies include wireless, microwave, fiber, Ethernet, GPON, hybrid and upstream interconnections. Logical `depends_on` means A has a mandatory dependency on Z. Several such edges are AND dependencies; OR/failover choices belong in separate service paths. Logical cycles are visited-set bounded, and logical dependencies alone cannot establish physical continuity. Shared infrastructure can be an explicit intermediate component present in multiple paths. Unknown ducts/power/shared risks are not inferred away.

The explicit administrator `import-existing` operation reads stored Site/Device/Asset/Sector/NetworkLink records only. It reuses canonical references and is idempotent. It does not scan addresses, poll equipment, invent strands, assume service paths or silently overwrite graph records after source changes. Inventory installation/status is not verified health. Source changes require reconciliation and, when relevant, renewed path verification.

## PostgreSQL data model

Migration0023 adds16 tables:

|Tables|Purpose|
|---|---|
|unified_nodes, unified_ports|Canonical component annotations, physical/GPON port labels and configured limits|
|unified_edges, unified_cables|Layered connectivity, existing link references, documented cable geometry/counts|
|unified_strands, unified_strand_ends|Unique numbered strands, allocations and A/Z terminations|
|unified_fiber_pairs, unified_pair_members|Two distinct strands in a cable; no strand can join two pairs|
|unified_fiber_joins, unified_join_ends|Splice/patch records and exclusive current endpoint memberships|
|unified_circuits, unified_paths, unified_path_hops|Customer/transport/dark-fiber circuits, alternate ordered paths and explicit strand hops|
|unified_observations|Timestamp/source-deduplicated read-only GPON observations|
|unified_associations|Existing documents, work orders and incidents related to components|
|unified_events|Append-only operator change history|

Foreign keys reference the existing inventory, customer, document, work-order and incident tables. Native uniqueness/check constraints enforce canonical identity counts, strand numbering/allocation consistency, path hop ordering uniqueness, fiber-pair memberships and GPON input uniqueness. A PostgreSQL trigger prevents strand numbers exceeding cable size. Event and path-hop history is append-only. No binary document copies, new customer accounts or competing work orders are introduced.

## Fiber documentation and allocation

Create a physical fiber edge, then a cable (1–1728 documented strands) with optional WGS84 longitude/latitude line geometry. Creation materializes unique numbered strands and two documented ends at the edge endpoints. Geometry is operator documentation, not a surveyed or geocoded route. Splices and patches connect exactly two distinct strand ends at the same component; endpoint membership cannot be duplicated. Pairs refer to two distinct strands of one cable. Administrators can map individual strand ends to existing optical/patch ports with revision checks and exclusive port membership. A termination-port change invalidates earlier end-to-end proof.

Strands are available, reserved, assigned or out of service. Reservations/assignments require an existing circuit, installed transport, current revision per selected strand and an authorized operation reason. A transaction locks rows in sorted order and validates the whole set before changing anything. Concurrent incompatible reservations have one winner. Expiration never silently frees a reservation. Release is blocked while an active documented path still uses the strand; retire/review the path first. These are NOC database changes only, not live allocation or provisioning.

Strand tracing follows cable and current splice/patch adjacency, bounds traversal, detects cycles/open ends and flags inconsistent joins. Retired joins retain their records; immutable creation/change events preserve old endpoint memberships. An approved existing optical-test document and nonfuture test timestamp can be referenced, but **a graph trace never claims verified optical continuity**. Real OTDR/power/continuity evidence must be reviewed by qualified personnel.

## GPON foundation and wireless telemetry

OLT PON, splitter input/output and ONT ports use explicit component/port types. Split ratios and subscriber/bandwidth limits are administrator-provided, not guessed defaults. Directed GPON relationships validate OLT→splitter→ONT identities/ports, avoid duplicate downstream inputs and prevent reuse of a connected splitter output. Multi-stage splitters and vendor-specific provisioning profiles require further design/validation; this is a foundation, not a live OLT management driver.

`POST /api/unified/observations` accepts the bounded, NOC-owned normalized read-only contract: status, optional RX/TX dBm, throughput/capacity Mbps, subscribers, loss percent and latency milliseconds. Missing fields remain null. Unknown fields, nonfinite/out-of-range values, future/naive timestamps and conflicting duplicate samples are rejected. Samples preserve source, timestamps and the original validated contract values. `noc-gpon-fixture-v1` is accepted only in explicit offline synthetic staging; `operator-recorded-read-only` records human-supplied evidence. **There is no live GPON connector, worker, endpoint, credential configuration or polling operation to enable.** No native vendor API compatibility is claimed; actual OLT/ONT integration remains a future approved read-only adapter task.

Existing MikroTik monitoring and Ubiquiti/Cambium/Racom/Aviat RF adapters are reused as stored sources. Node context exposes canonical device and IPAM references; fresh existing RF analytics points supply radio signal/SNR, TX/RX throughput, channel data, utilization and adaptive observed capacity. The latest invalid/stale point suppresses current values. No duplicate RF poller is added. Configured link capacity, estimated capacity and observed capacity/traffic are separately named. The aggregate throughput field is the maximum of TX/RX directions, not an invented RF total. Missing telemetry is unknown, never zero/healthy. GPON observations are fresh for300 seconds; old samples retain their timestamp but do not populate current measurements.

## Customer paths, impact and outage correlation

Circuits reference stable imported CustomerService IDs, or represent infrastructure/transport/dark-fiber circuits without a customer. Several circuits and several paths per customer are allowed. Ordered physical paths must start at a documented upstream/core/headend and end at the circuit's customer service when one is assigned. Paths reject missing, noncontiguous or cyclic hops. Fiber hops require the exact cable strand/circuit allocation and documented joins between consecutive fiber segments. Hybrid conversion points must be explicitly modeled; no connection is inferred from geographic proximity.

Completeness is an explicit operator attestation, initially false. After documenting paths, an administrator can record a revision-checked completeness review. Alternative service is considered preserved only when an active, intact path has a current **operator-recorded end-to-end functioning verification** expiring within24 hours. This is not an automatic ping or a declaration that the fiber has been optically tested.

Verification captures node/edge revisions, asset lifecycle/location, device-site and customer mapping references, and relevant splice memberships. Changed documentation/equipment/mapping requires re-verification. Retirement, missing assignments, changed splices and fresh contradictory GPON/device failures prevent preserved-service claims. A device coming back up after a recorded disruption does not by itself reuse old end-to-end proof. Create a freshly reviewed path record and retire superseded paths; do not silently rewrite hop history.

Impact preview distinguishes potentially affected, estimated/unknown, preserved-by-current-verified-path and no-documented-failed-dependency. Verified functioning alternatives exclude the service from affected/estimated lists, even when another circuit/path fails. Customers are deduplicated using active, nondeleted Powercode services. Incomplete topology, unverified alternatives and changed dependencies remain uncertain; modeled impact is not a confirmed observed customer outage or definitive root cause.

`GET /api/unified/incidents/ID/impact` reviews an existing NOC-97 incident. Explicit associations and the most specific device/link/site scope are used, with mandatory-dependency closure. A customer served by a tower is not automatically treated as physically located there. Missing canonical scope returns insufficient topology. Historical resolved incidents do not generate current notification candidates. Returned service/customer IDs are a protected **review package** for NOC-96's existing incident/consent/approval workflow; no notification plans, SMS/Slack deliveries or automatic dispatches are created. Existing legacy correlation/notification policies are preserved; automatic replacement of every legacy impact calculation is not claimed.

## Capacity, qualification and recommendations

Capacity reports show separate configured/estimated/observed Mbps, current utilization only when supported, and explicit available/reserved/assigned/out-of-service strand totals. Circuit bandwidth is documentation, not measured demand. PON configured limits, documented connections and fresh subscriber/optical values remain distinct; unknown limits never imply spare capacity. Spare strands alone do not establish spare OLT ports, splitter capacity, light levels or customer connection rights.

Preliminary qualification consumes NOC-103 coverage polygons and stored tower/sector/native RF evidence, nearby documented fiber line geometry (within1 km), installed strand counts and reachable documented PON resources. Results are potentially serviceable, site survey required, construction required, capacity upgrade required, not currently serviceable or insufficient information. Active/planned service-area evidence is distinguished. Source/obstruction/LOS, engineering feasibility, connection rights, service date and construction cost remain unverified. Existing signup qualification snapshots include the unified review when graph data exists; applications are never automatically approved or provisioned.

Recommendations cite inventory/topology/telemetry evidence: missing documentation, limited physical degree, common recorded path dependencies, strand exhaustion and fresh observed congestion. Common-path intersections cite the circuit/path/component IDs; they do not prove all unrecorded paths or shared ducts are known. Degree-one warnings are possible dependencies, not mathematical network cut proofs. No failure probabilities, optical readings, RF measurements, upgrade costs or automatic changes are invented.

## Private map and operational workflows

Open `/?unified=home` after NOC login. The responsive SVG map supports pan/zoom, technology/site/state/health/capacity filters, source service-area overlays, cable geometry, stored alarms, component drilldowns and customer-impact highlighting. Distinct line styles separate wireless, microwave, fiber, GPON, hybrid and logical relationships. It uses **no public map tiles** or external map requests. Co-located symbols are offset for readability; missing coordinates are explicitly schematic. Exact customer premises are not fabricated from their serving tower. Search/drilldowns and paginated tables remain usable without a geographic coordinate.

Administrators document components, ports, connections, cables, strand pairs/joins/reservations, circuits/paths, state changes and existing document/work/incident associations. Reasons, explicit documentation approval, operation keys, revisions and exact retry envelopes protect writes. Retry data is held in memory only; there is no full offline synchronization. Refresh history before discarding an uncertain result.

Network operators can inspect private topology/impact/capacity; structural edits require administrator authorization. Technicians cannot browse the unrestricted graph: their existing field work-order page exposes only component references associated with an authorized assignment. Existing asset/tower QR labels continue to identify the same canonical inventory. NOC-102 file storage, financial/document visibility, scanning/approval and protected download routes remain authoritative; associations do not copy files or bypass permissions. NOC-99 context returns existing prefix/interface IDs rather than publicly disclosing management IPs.

## Limits, backup and migration operations

Traversal bounds:20,000 nodes,50,000 edges,10,000 circuits,20,000 paths,1,280,000 hops; strand tracing100,000 strands/200,000 ends loaded and10,000 visited ends. Map pages are1000 edges plus endpoint/isolated component context; cable strand pages100. Capacity requests require at most1000 edges. Qualification is bounded to1000 cables/areas,2000 reachable nodes and500 PON ports. Recommendations return at most1000 items. Bounds reject or explicitly mark truncation; use narrower filters/partitions. Manual GPON historical samples have no unattended pruning worker; an approved retention/archive strategy is required before sustained collection.

Before upgrading, disable operator writes, take the existing PostgreSQL backup and paired NOC-102/NOC-103 private-file/key backups, record image/commit manifests, then run `alembic upgrade head` with the migration role. Confirm heads, `alembic check` and health before resuming internal use. The0023→0022→0023 round trip is for a disposable empty validation schema only. Do not downgrade/drop populated unified tables for code rollback. Preserve schema/data and return to approved prior images, or restore into a **new isolated database** using matching images; never overwrite a production/source database. New graph data has no separate binary store.

## Validation and remaining activation gates

The draft PR contains exact final counts, commit/workflow links and measured performance. Synthetic PostgreSQL cases cover identity reuse, fibers/pairs/joins, native constraints/history, atomic/concurrent reservations, topology/path continuity, redundant/shared failures, stale/missing/conflicting GPON data, native RF reuse, qualification, source incidents, document/work/technician restrictions, mapping changes and proof invalidation. Browser acceptance exercises the mobile map, fiber traces, verified alternate exclusion and audited reservation. Existing NOC-95–103 regression gates remain enabled.

The rollback-only benchmark uses40 POPs,400 infrastructure devices,500/1000 customer service locations,1000/10,000 strands and1000/2000 paths. Measurements are PostgreSQL stored-record/map-page/impact/trace timings and process RSS, not browser FPS, HTTP concurrency, real optical continuity, live firmware compatibility or production sizing. No external device/provider calls occur.

**GO for synthetic development acceptance only; NO-GO for live provisioning/production activation based solely on this evidence.** Remaining prerequisites: reconcile/approve real physical and logical inventory; validate strands, splices, optical evidence, GPON models and vendor read-only interfaces on explicitly approved devices; independently validate failover and shared risks; approve service-area/capacity/engineering/notification policy; rehearse protected-server deployment, access control, backups and restore; conduct actual map/HTTP/operator load acceptance and historical retention design. Generic GPON records are not a claim of compatibility with a specific OLT/ONT firmware. Import corrections, multi-stage splitter modeling and industrial GIS/OTDR integrations require further controlled work.

No production systems, live equipment, OLT configurations, routing/RF settings, production Powercode, real messages, service activation, other repositories or PR merges are touched. RF self-healing remains simulation-only.

Optional field context reads have a five-second deadline and show a local retryable error. A failed optional panel cannot discard the technician's main authentication session or inspection draft. Backend authorization still applies to each read; primary field actions retain existing authentication handling. The synthetic browser regression injects an auxiliary401 and verifies draft preservation plus an independently authorized primary work-order read.
