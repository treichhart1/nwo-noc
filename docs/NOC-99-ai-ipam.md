# NOC-99 — AI-assisted IPAM

## Baseline and scope

Branch `feature/noc-99-ai-ipam` starts at NOC-98 PR #102 head
`5a8c1a893d16082cffe2b4604df9275e2282a5b4`. PR #102 was reviewed open,
draft and unmerged; GitHub Actions #514 passed all eight jobs. Preserve all
upstream dependencies: integrate their existing stack in order, through NOC-95
#99, NOC-96 #100, NOC-97 #101, NOC-98 #102, then this PR. No PR was merged.
Application version remains `3.2.1-rc.3`.

IPAM manages **NOC documentation only**. It never writes router, switch, radio,
DHCP, DNS, VLAN or routing configuration. There is no network scanning facility,
automated allocation deployment or external AI service. Existing RF self-healing
remains simulation-only. External notifications remain disabled.

## Architecture and schema

Additive Alembic migration **0018_ai_ipam**, parent **0017_tower_health**, adds:

- `ipam_vrfs`: routing domains and optional documented route distinguisher.
- `ipam_vlans`: site/VRF-scoped VLAN IDs, purpose and lifecycle status.
- `ipam_interfaces`: stable per-device interface identity, VRF, VLAN and parent.
- `ipam_prefixes`: PostgreSQL `cidr`, explicit block/subnet hierarchy, tower,
  existing backhaul link, VLAN, documented owner/scope and gateway.
- `ipam_pools`: bounded `inet` ranges and DHCP/NAT/infrastructure/customer purpose.
  NAT references document a relationship; these records are not NAT attribution logs.
- `ipam_assignments`: PostgreSQL host `inet`, reserved/assigned/released state,
  equipment/interface/service/pool associations and DNS name.
- `ipam_dns_records`: documented A/AAAA records derived from approved assignments;
  not a live DNS configuration database or zone-transfer implementation.
- `ipam_observations` and `ipam_discovery_batches`: immutable keyed, bounded
  recorded evidence and replay hashes, independent from authoritative allocations.
- `ipam_import_batches`: reviewed preview, errors, canonical digest and application state.
- `ipam_changes`: before/after documentation history, operator, reason and time.
- `ipam_read_budgets`: shared PostgreSQL request budgets, independent of Redis.

Existing Site, Device, NetworkLink and Powercode CustomerService records are reused.
No tower/device/customer inventory is duplicated. Interfaces are NOC documentation
referencing equipment, not new device inventory or deployed router interfaces.

A partial unique index prevents two active assignments/reservations for the same
host address **within a VRF**. Overlapping RFC1918/IPv6 plans in separate VRFs are
valid. VLAN IDs are unique by **VRF + site + VID**, not across the entire WISP.
PostgreSQL check constraints and serialized trigger validation protect prefix
containment, routing contexts, usable addresses, VLAN/interfaces and pool bounds
including direct SQL attempts. Prefix/VLAN/interface context changes require new
reviewed documentation rather than silently moving existing assignments. Released
assignment rows cannot be reactivated or rewritten. Reservations use transactions,
a shared advisory lock and the native unique index. Four actual concurrent
transactions are tested for one winner and three conflicts.

## IPv4 and IPv6 allocation rules

Parents must be documented blocks in the same VRF. Children are strict subprefixes;
sibling overlap and unrelated same-context overlap are rejected. Only leaf subnets
receive host assignments. A parent is not created retrospectively over existing
unrelated prefixes: document the hierarchy first. CIDRs must be canonical; host bits
are rejected rather than silently truncated. Native GiST prefix lookup and indexed
routing-context/address queries avoid textual IP comparisons.

IPv4 networks and broadcasts are withheld for prefixes shorter than /31. Both /31
addresses and a /32 host can be documented. IPv6 subnet-router anycast is withheld
for prefixes shorter than /127; /127 and /128 permit their explicit host addresses.
Network/gateway/address families must agree. A documented gateway is withheld from
ordinary allocation; use assignment kind `gateway` to document it explicitly.
Pools are ordered, nonoverlapping usable ranges inside a leaf subnet and exclude its gateway. Static candidates skip documented and observed pool ranges; allocating inside a documented pool requires selecting that pool explicitly. Gateways,
network/broadcast addresses and observations are excluded from automatic candidates.

Candidate selection walks sorted occupied numeric intervals; it does not enumerate
a /64 or expand a large prefix into individual addresses. Prefix suggestions jump
past existing child ranges. They exclude **documented** use, not undocumented real
use. Operator approval, evidence review and a meaningful reason remain mandatory
before saving NOC documentation. No recommendation claims an address is safe to
configure on real equipment.

Capacity is documented active assignments/reservations plus withheld gateways,
not observed reachability or live DHCP pool usage. Parent utilization counts
allocated child ranges; leaf utilization counts documented hosts. Hierarchical
parent/child capacities must not be added as if they were independent free space.
IPv6 counts are decimal strings so JavaScript cannot lose integer precision.
Routing contexts are separate capacity domains even when address ranges overlap.

## MikroTik and discovery

The integration is disabled by default:

```
IPAM_DISCOVERY_ENABLED=false
```

No automatic worker enables IPAM discovery. The administrator collection API also
requires existing MikroTik enable/approval flags, an explicitly configured inventory
device, exact HTTPS origin allowlist, TLS verification, the existing emergency
integration interlock and read-only credentials. Polling is at least 300 seconds
apart and protected against overlapping collectors. Existing bounded GET-only
transport limits apply: connect/read timeouts, at most two retries, response-size
and deadline limits. Only fixed documented resource paths are available; there is
no arbitrary URL, RouterOS command, CLI or write-method parameter.

RouterOS 7 REST resources: system identity/resource; interface, VLAN and bridge;
IP/IPv6 addresses and routes; routing tables and OSPF neighbors; DHCP server networks,
pools/leases; ARP and IPv6 neighbors. Unsupported resources are explicit partial
results, not empty authoritative inventories. Malformed batches do not partially
import. Original credential/comment fields are not retained. Recursive/multiple
next hops are safe uninterpreted evidence rather than invented literal gateways.

**RouterOS 6 has no REST API.** This milestone accepts validated recorded read-only
API `/print` response contracts for RouterOS 6 (routing marks) and RouterOS 7
(routing tables). It does not ship or claim a tested live RouterOS 6 binary API or
SNMP transport. Native monitoring interfaces, firmware capabilities and permissions
still require separately approved validation. Unknown/unsupported fields are not
fabricated. No live router was contacted during development.

Administrator-recorded batches require an explicit selected NOC VRF and device.
Router table names are evidence, not automatic proof of NOC routing-domain identity.
Mixed table/mark contexts must be split and reviewed independently. Maximums are
16 resources, 2,000 rows/resource and 5,000 normalized observations/batch. Recorded
input older than 30 days or from the future is rejected; observations older than
15 minutes are stale. Event keys are idempotent and conflicting replays rejected.
The inventory preview is unverified documentation and never allocates automatically.

## Reconciliation and recommendations

Assigned/reserved records are authoritative NOC documentation. DHCP leases, ARP,
neighbor sightings, routes and interface-address reports remain observations.
An absent observation never releases an allocation or proves an address unused.
Even retained stale observations block automatic host suggestions pending review.
Unmanaged addresses have unknown ownership. The lookup API distinguishes documented
assignments/reservations, observed-unverified addresses, documentation-only available
candidates and unknown addresses outside managed leaf space.

Fresh conflicting interface-address claims in one VRF are **potential conflicts**,
not confirmed configuration errors. Shared/virtual addresses, link-local scope and
incorrect routing-domain mappings require investigation. Duplicate active NOC
assignments are prevented by PostgreSQL instead of being accepted and silently
"resolved". No telemetry disagreement changes inventory, addresses, routing or VLANs.

Explainable deterministic recommendations include documented exhaustion (85%; expected full single-host/two-endpoint leaf prefixes are excluded),
fragmentation, reservations older than 30 days, unverified infrastructure, possible
address conflicts, gateway/VLAN documentation differences and missing point-to-point
endpoint documentation. Each includes evidence, confidence, priority, an investigation
and an approval requirement. Age is not proof a reservation is unused. A route next
hop need not equal a subnet gateway, so a difference is an investigation, not a
configuration fault. Plans cover tower/wireless/fiber/hybrid growth through existing
site, link, interface and service associations. No failure probability, automatic
renumbering or forecast without evidence is generated.

## Customers, towers and topology

Customer allocations reference stable NOC service IDs linked to existing Powercode
external account/service identifiers. They require active, nondeleted, fresh (48-hour)
reviewed mappings consistent with serving tower, equipment and sector. Customer CPE
association requires a matching stable equipment reference and serving-device
relationship; same-tower proximity alone is insufficient. DHCP observations never
establish that a customer has a fixed IP. Billing data is not written or queried
from a production source. Wireless/fiber/hybrid service identity remains in the
existing customer system.

Point-to-point prefixes reference existing NetworkLink endpoints and permit addresses
on either validated endpoint. The dashboard exposes tower filters, prefix/VLAN/VRF
relationships, interface records, recorded routes/OSPF evidence and links to existing
incident investigations. It never changes topology, infers guaranteed root cause or
sends customer notifications.

## Dashboard workflow

Open **AI IPAM** as an operator/admin. Choose a routing domain; filter prefixes by
family, ownership scope or prefix, and assignments by IP/prefix/DNS/stable customer
or service identifier, VLAN, tower, equipment, customer or documented state. Prefix
and assignment tables are paginated. Inspect a subnet for capacity, pools, evidence
and recommendations. Review observations separately; missing/stale ownership is
explicit. Inspect assignment history for operator reasons and DNS documentation.

Enter a meaningful **Change reason** and approve **NOC documentation only** before
using dashboard creation, reservation, assignment or release controls. Interface IDs,
pools and customer service IDs refer to existing reviewed records. Prefix planning
suggestions populate a review form, not equipment configuration. The backend also
supports assigning a reservation to reviewed equipment/customer ownership with an
audited state update, and VLAN planned/documented/retired status changes.

## CSV import/export

Import maximum: 500 rows / 256,000 characters. Row types `prefix` and `assignment`:

```csv
type,vrf_id,network,kind,scope,owner
prefix,1,192.0.2.0/24,subnet,documentation,Synthetic plan
```

```csv
type,prefix_id,address,state,kind,owner,device_id,service_id,dns_name
assignment,12,192.0.2.10,reserved,management,Synthetic router,42,,router.example.invalid
```

Optional prefix columns: parent_id/site_id/vlan_id/link_id/purpose/gateway. Optional
assignment columns: interface_id/pool_id. IDs must already exist; import parent
hierarchies before dependent children and assignments. Do not guess IDs created by
a prior preview, as PostgreSQL sequences are not rolled back. Empty address requests
a reviewed documentation candidate. Preview performs actual validation in rolled-back
savepoints, including within-batch duplicates; no addresses or audit changes are
committed by dry-run validation. Errors are private, bounded row reports.

Applying requires the unchanged canonical digest, explicit approval and a reason,
within 24 hours. The transaction revalidates against current allocations. Any failure
rolls back the entire application, including earlier rows. Applied record IDs remain
in the batch for provenance. An applied batch cannot be applied twice. This is atomic
failed-import rollback, not a destructive "undo" of later independent operator work;
use audited release/correction workflows for subsequent changes.

Authorized infrastructure CSV exports exclude customer-linked/customer-kind records.
Customer allocation export requires an administrator. Exports are bounded to1,000
rows and neutralize spreadsheet formula prefixes. No billing/contact data or NAT
attribution logs are exported. Endpoints return private data only after authentication.

## Security, migration and recovery

All `/api/ipam` routes require operator/admin access; viewers and unauthenticated
sessions are denied. Native/recorded discovery is administrator-only. Shared
PostgreSQL budgets allow120 requests per operator per minute across processes and
survive Redis interruption. Lists and analysis are bounded; use VRF/prefix/device
filters for larger inventories. Private validation errors never echo submitted
credentials or raw SQL. Secrets use existing secure configuration and TLS transport;
IPAM creates no secret store. Technician reasons use the shared credential filter.

Migration18 was upgraded, downgraded to17 in an empty disposable module fixture,
then upgraded again, and `alembic check` detected no drift. No prior migrations were
modified. Follow [Ubuntu installation/recovery](NOC-84-installation-and-recovery.md)
and existing release backup/rollback procedures. Back up the complete database,
configuration and matching images before upgrade. A matching pre-upgrade backup and
application image is the operational rollback. Downgrading18 destroys its new IPAM
records and audit history; never use it as a production rollback shortcut. All tests
use disposable databases and synthetic information; no production data is overwritten.

Observation/event and IPAM change history are retained in this milestone; no silent
age-based deletion or auto-release occurs. Plan measured retention/archiving and
long-duration dense-observation load testing before a live pilot. Read budgets can
be maintained with ordinary database operations; do not remove audit/ownership records
without an approved archival policy.

## Validation and activation prerequisites

Synthetic coverage includes IPv4/IPv6 host and prefix rules, hierarchy, gateways,
public planning, VRF overlap, VLAN reuse, pool boundaries, DHCP/ARP/OSPF observations,
RouterOS6/7 differences, malformed/unsupported data, stale ownership, replay, four
concurrent reservations, database guards, customer mappings, import preview/rollback,
private exports and authorization. Browser acceptance covers navigation, subnet
investigation, observed ownership and approved CSV allocation/history/release.
Existing RF, Powercode, SMS/Slack simulation, Down Detector and tower-health regressions
remain in CI. Exact final counts, commit SHA and GitHub results are in the draft PR.

Measured local workload: 40 POPs, 400 devices, two routing domains, 1,004 prefixes.
At 1,000/10,000 assignments, bulk inserts took 230.27/1,929.65 ms; 100-row pages
5.13/3.71 ms; 50-prefix pages including batched utilization 27.14/21.27 ms; prefix
containment 2.58/1.19 ms; single-prefix utilization 3.27/2.52 ms; planning 6.58/4.88 ms.
Nine SELECTs covered these measured read operations; peak RSS 84,108/94,696 KiB;
external calls zero. Local IPAM acceptance: 66 passed. Chromium workflow: one passed.
Earlier full regression run: 1,118 passed before the final two pool-safety cases were
added; exact final full-suite and CI counts are recorded in the draft PR.
This is stored-document performance with PostgreSQL constraints, not live discovery
throughput, native polling performance or a production sizing guarantee.

**NO-GO for production or live discovery.** Before any approved pilot: validate the
one-device endpoint/VRF/interface mappings, minimum-privilege credentials, TLS/allowlists,
emergency disable and accurate native RouterOS schemas. RouterOS6 native transport,
firmware/MIB compatibility, multi-domain observation provenance, real DHCP/bridge
capabilities, very large/dense histories and standalone-server operation remain
unverified. Generic CSV/recorded contracts are not claims of vendor compatibility.
No unrestricted scans, live configuration changes, external notifications or automatic
RF remediation were performed or enabled.
