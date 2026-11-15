# NOC-105: local LiDAR, terrain and advisory RF planning

## Dependency and scope

Branch `feature/noc-105-lidar-rf-planning` is directly based on unmerged draft NOC-104 PR #108, head `e41a2d89c9cd99a416bcf33216b6255af7286580`, CI #542 successful. Preserve the full oldest-first stack: NOC-95 #99 → NOC-96 #100 → NOC-97 #101 → NOC-98 #102 → NOC-99 #103 → NOC-100 #104 → NOC-101 #105 → NOC-102 #106 → NOC-103 #107 → NOC-104 #108 → this PR, including the earlier RF/NOC-83–94 dependencies. None is merged by this milestone. Revalidate descendants after upstream changes.

This is an integrated private NOC module at `/?planning=home`, not another application or fiber designer. Existing Site, Sector, Device and Asset IDs remain authoritative. Planning proposals/snapshots never update production inventory, antenna settings, customer assignments or configuration. Application version stays3.2.1-rc.3. Schema head0024_rf_planning follows0023_unified_network.

## Data, file formats and imports

Six PostgreSQL tables: `planning_datasets`, `planning_tiles`, `planning_profiles`, `planning_scenarios`, `planning_jobs`, `planning_calibrations`. Native foreign keys preserve existing inventory/actor/measurement/survey relationships. Dataset checksum is unique; tile position is unique per dataset. Profiles, scenarios and calibration evidence have PostgreSQL append-only triggers. Every operator mutation uses the existing field/inventory lock, actor-scoped operation receipt and audit trail. Result jobs retain their original input fingerprint, engine version and source hashes.

Only an administrator can upload local GeoTIFF (`.tif/.tiff`), LAS or LAZ. Multipart metadata requires name, ground/surface kind, explicit elevation units, vertical datum, source attribution, acquisition date and WGS84 `[west,south,east,north]` bounds. Files have signature validation and a streamed64MiB default limit (configurable1KiB–128MiB). GDAL opens only the GTiff driver, no VRT/archives/URLs/virtual filesystem paths. PROJ network grids are disabled. No dataset or tile downloads occur.

Processing verifies CRS, finite WGS84 transformed bounds, declared coverage, north-up georeferencing, one numeric elevation band and raster dimensions. CRS metadata and horizontal native units/resolution are retained; elevation units explicitly support metres, international feet (.3048m) and US survey feet (1200/3937m). Coordinates use pyproj with longitude/latitude axis order. **Vertical datums are declared evidence, not automatically transformed or verified.** Mixed dataset datums are rejected. Reconcile surveyed endpoint elevations to that datum before use.

LAS/LAZ processing uses laspy/lazrs in50,000-point chunks, at most2million points and1million output cells. It requires a projected, metre-based horizontal CRS. Ground output bins classification2 minimum heights; surface output uses maximum non-noise points (excluding classes7/18). One-metre bins are an explicit adapter rule; there is no terrain interpolation, automated vegetation classification or claim that sparse points produce a complete DTM/DSM. Empty cells remain nodata. Header version, point format, counts, source bounds and derivation method are preserved. Other classifications/CRSs/large point clouds require approved offline preprocessing.

Raster indexing uses512-pixel tiles and transformed bounds, capped20,000 tiles/100million source pixels. Sampling reads128×128 windows through a64-entry per-job LRU cache, never the whole raster. Nearest-pixel sampling and finite elevation validation preserve missing data. Selected dataset order is the explicit priority in overlaps; overlap IDs/truncation are recorded. No hidden merging or fabricated coverage fills gaps. Existing PostgreSQL uses B-tree bounds indexes; **PostGIS raster/GiST is not installed or required**. Migration does not change extensions. Native PostGIS tiling can be a future optional adapter after deployment validation.

Raw/derived files live outside PostgreSQL under `PLANNING_STORAGE_ROOT`, default `/var/lib/nwo-noc/planning`, private directory/nonroot ownership. Basenames are application-controlled, symlinks rejected, uploads0600. Source and derived SHA-256 hashes are checked before analysis. Metadata endpoints never provide unrestricted file URLs; reports require existing NOC authentication. Originals and failed-import evidence are retained. Operators must monitor disk use and plan an audited orphan/retention policy; no unattended file deletion is implemented. A failed upload transaction may leave a private unreferenced blob for that policy.

## Radio profiles and geometry

Catalog families include Ubiquiti Rocket AC/Gen2, PowerBeam, AF11, Wave Pro/LR; Cambium ePMP4600c/4625; Racom Ray3 and Aviat. Families do not contain invented manufacturer specifications. Administrators supply cited, validated profiles: frequency/channel width MHz, TX power dBm, gain dBi, losses dB, receiver sensitivity dBm and polarization. Missing values remain null. The same contract accepts other radios,11/18GHz backhaul and60GHz profiles.

Scenario emitters contain explicit coordinates, mounting height metres AGL, optional surveyed ground elevation, tower height, azimuth, horizontal/vertical widths, mechanical/electrical downtilt, existing inventory IDs and provenance. Existing tower coordinates and sector/device mappings are validated; edits require a separate unlinked proposal. The dashboard can copy documented tower/sector geometry, converting inventory feet to metres, but does not infer a mounting height or antenna gain. Mounts above a supplied tower height are rejected. Proposed sites do not create a second inventory.

## LOS, Fresnel and propagation

WGS84 geodesic distance/profile positions use pyproj.Geod. Endpoint antenna altitude is sampled ground plus documented AGL. Interior obstructions use ground and the greater available surface value. Missing ground samples prevent LOS/coverage classification. Partial/missing surface coverage is explicitly flagged: **ground clearance does not verify vegetation/building clearance**.

At distance d, first Fresnel radius is `sqrt(lambda*d1*d2/(d1+d2))`. Effective-Earth bulge is `d1*d2/(2*k*6371008.8)`. Refraction factor k defaults to4/3 as a documented configurable assumption; permitted0.5–3. Clearance is assessed against the ray and configurable Fresnel fraction (default0.6). Profiles use3–513 discrete samples. Spacing is returned; narrow obstacles may fall between samples. No verified optical/radio continuity is claimed.

FSPL is `32.447783 +20log10(distance_km)+20log10(frequency_MHz)`. Link budget is TX power + TX/RX gains − TX/RX losses − FSPL − explicitly selected modeled losses. Fade margin is predicted receive level minus supplied sensitivity; the required planning margin defaults10dB, not a reliability guarantee. A configurable dominant knife-edge approximation uses the standard v parameter and `6.9+20log10(sqrt((v-.1)^2+1)+v-.1)` above v=−.78. It represents a **single dominant edge**, not a validated multi-edge/clutter/Longley-Rice model. Multiple diffraction/reflection is unmodeled. Obstructed FSPL is explicitly an upper bound.

Documented horizontal beamwidth gates candidate directions, without inventing side-lobe patterns or attenuation curves. Vertical gating requires both downtilts and width; otherwise boresight gain is an explicitly uncertain upper bound. Missing polarization compatibility remains uncertain. Atmospheric loss is an optional operator-supplied dB/km assumption. **No validated rain-fade/gas/availability model is implemented**, especially consequential for60GHz and licensed microwave. No theoretical link capacity is presented as measured throughput or customer speed. Regulatory EIRP, licenses, rain availability and engineering approval require separate review.

## Jobs, repeatability, security and recovery

API access reuses NOC RBAC: admin/operator can inspect planning; structural writes/imports/cancellation/calibration require admin. Field technicians cannot browse private planning/topology. Existing field request budgets apply. Errors omit submitted sensitive fields. No public/signup administrative API is added. Protected coordinates remain internal; no external tiles/geocoding/downloads are used by this module.

Jobs are durable PostgreSQL rows with `FOR UPDATE SKIP LOCKED` claims,120-second leases, unique fencing tokens, cancellation checks and a three-attempt crash-recovery cap. Only the current token may publish final results. Progress commits in a separate session; results are published transactionally, not incrementally as usable predictions. Resource-limited subprocesses have90s wall time,80s CPU,1.5GiB address space,128MiB file and96-descriptor limits. No user command/shell is executed. Database interruptions leave recoverable leases; stale workers cannot overwrite newer results. Cancellation is cooperative between coverage batches and before publishing; an in-progress native decode may run until the90s deadline. Dataset imports cap pending work at8, all planning jobs16. Durable worker recovery emits audit events.

Bounds:1–100km links,16 emitters,32×32 grid,4096 rays and150,000 profile samples per job. Larger networks are partitioned; coarse grids are not premises-level guarantees. Jobs/scenarios/datasets/calibrations are paginated50/page. Catalog and qualification searches are bounded500 profiles/100 recent jobs; truncated searches are not complete network-wide analyses. Only current ready dataset/inventory snapshots may contribute to qualification. Retiring a dataset or changing canonical mappings invalidates current use; old reports remain historical. Same-scenario job/result reuse is the cache; changed assumptions require a new immutable scenario. There is no cross-operator/global automatic model tuning.

### Start and stop

After the normal documented Ubuntu installation/migration and private volume backup configuration:

```
# Default is disabled. Opt in to an isolated, separately bounded CPU worker.
docker compose -f deploy/compose.isolated.yml --profile planning up -d planning-worker
# For the standard development Compose definition:
docker compose --profile planning up -d planning-worker
# Emergency stop affects local planning only, not devices:
docker compose --profile planning stop planning-worker
```

Use the existing installer-generated `NOC_ENV_FILE`, database/image/project variables when invoking isolated Compose, exactly as for `scripts/release/manage.sh`. The profile is not active by default. Backend and planning worker share `planning_terrain`; isolated network is internal-only. Alternatively run `PLANNING_WORKER_ENABLED=true python -m app.planning.worker` from the backend with its migration/current runtime environment. `--once` is useful for bounded approved processing; `--health` checks a private heartbeat (stale after150s). A configuration flag alone does not prove worker liveness: inspect job progress, leases, heartbeat and service health. Do not run new migrations with the restricted runtime role.

## Dashboard, qualification, comparison and calibration

`/?planning=home` provides terrain metadata/boundaries, profile catalog, existing tower/sector selection, explicit geometry/assumption editors, queued job progress/cancellation, interactive private coverage/LOS map, frequency/status filtering, per-emitter evidence and terrain/Fresnel profiles. Coverage cells distinguish preliminary margin/clearance, obstruction risk, weak/outside-beam and insufficient data. Signal dBm, loss, fade margin and overlap counts are predictions. The map has no external base tiles. Editors expose the closed JSON contracts for advanced multi-sector/proposed-location planning; guided antenna forms and richer network-wide tiling remain future usability work.

Same-grid comparisons require matching coordinates, receiver height/gain/loss and grid size; differences and changed assumptions are explicit. JSON exports are authenticated private reports suitable for operator-controlled printing/sharing. There is no public sharing URL.

NOC-103 qualification snapshots now include an advisory planning review from explicitly qualification-enabled completed coverage scenarios. It uses the nearest grid sample, not an exact-address terrain computation, and never approves an application. Candidates expose RF classification and current documented sector/backhaul evidence through NOC-104. Configured, estimated and measured capacity remain separate. Fresh observed utilization≥90% flags capacity limitation; unknown capacity cannot imply spare bandwidth. Site surveys remain required. No automatic Powercode changes or notifications occur.

Calibration links predictions to existing normalized RFAnalyticsPoint IDs or NOC-103 survey records, records measured versus predicted dBm/error and preserves history. Administrator must attest receiver/location/time/direction alignment; explicit measured receiver coordinates must be within25m of the selected modeled sample. RF source must match the supplied receiver device and be valid, measured and nonfuture. Surveys require stored signal/location evidence. Historical RF records cannot establish current simultaneous performance. Calibration is advisory; no fitting/tuning parameters happens automatically.

## Backup, migration and limitations

Back up PostgreSQL plus the private terrain volume, existing NOC-102 documents and NOC-103 PII key/files together. Retain source checksums, attribution and matching engine/image manifests. Restore into a new isolated database/volume; verify raster hashes and schema before resuming work. Never overwrite source/production data. The0024→0023→0024 test is for an empty disposable schema. Do not downgrade/drop populated planning tables for operational code rollback; retain schema/files or restore an approved matching backup to an isolated target.

Remaining validation: real regional DTM/DSM/LAS quality and vertical datums; representative vendor antenna/radio profiles; field LOS/RSSI and seasonal clutter; rain/gas/60GHz models; exact-premises sampling, multi-edge propagation, sustained HTTP/operator/concurrent worker load and operational disk retention. No field accuracy, real radio firmware compatibility or production-readiness claim follows from synthetic tests. RF self-healing remains simulation-only. No live equipment, deployments, customer approvals, messages, production Powercode or other repositories are used.

Exact test counts, benchmark measurements, commit and GitHub Actions results are in the draft PR's final validation report.

Failed imports can be explicitly corrected and requeued with administrator `POST /api/planning/datasets/{id}/review-retry` and the full DatasetInput metadata/reason/operation key. Previous failed metadata and jobs are retained. Only failed datasets permit correction; ready sources must be retired and reviewed independently. Correction history is bounded. The dashboard's upload editor accepts the same metadata shape; API review/retry currently requires the documented private API rather than a separate guided correction form.
