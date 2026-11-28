# Regional cellular dataset contracts and licensing

No proprietary CellMapper scraping, external API calls, automatic downloads or dataset redistribution occurred. Use local decompressed regional extracts. Operator must review source authenticity, legal terms and redistribution separately; an approval reference/local hash is not vendor authenticity proof.

## OpenCellID

Reviewed [official database format](https://wiki.opencellid.org/docs/downloads/database-format), [downloads](https://wiki.opencellid.org/docs/downloads/overview), [daily changes](https://wiki.opencellid.org/docs/downloads/daily-changes), [license/attribution](https://wiki.opencellid.org/docs/attribution). Exports use estimated cell locations, not surveyed physical towers. Availability varies geographically; absent data does not establish absent coverage. API/download access may require an account/token and current service conditions; application imports do not require credentials. Operator downloads only the needed source then extracts Ohio/Indiana locally before upload. Review current download/access/update terms; do not fetch nationwide data automatically.

License **CC BY-SA4.0**. Retain source notices, credit OpenCellID, link source/license and describe changes. Share-alike/database obligations can affect redistributed derivatives; separate legal/operator approval required. Imported rows remain in dedicated observation tables, never merged into authoritative NWO inventory. Private UI/API returns attribution/license/version alongside points. No public export feature was added.

Exact header (15 columns):

```csv
radio,mcc,net,area,cell,unit,lon,lat,range,samples,changeable,created,updated,averageSignal
```

Current implementation accepts documented **LTE** semantics: MCC/MNC integers, TAC, full28-bit ECI, estimated coordinates/range, source observation timestamps. Other technologies are counted as unsupported/skipped, not reinterpreted. Official generic export documentation does not establish NR/5G-specific cell fields; **NR dataset import remains BLOCKED**. PCI is not a globally unique cell ID. No eNodeB/gNodeB derivation. MCC/MNC numeric source may not preserve leading-zero length; carrier identity remains operator-reviewed, not guessed. Deprecated averageSignal is not live RF telemetry. Zero range is supplied estimate, not surveyed precision.

Limits: decompressed JSON-wrapped CSV up to1.5MB/5000 rows, no archives, exact header, finite coordinates, timestamps no later than acquisition, exact/conflicting duplicate handling, bounded preview and immutable fingerprint approval. Bounding rectangles: Ohio west-84.9/south38.3/east-80.5/north42; Indiana-88.1/37.7/-84.7/41.9; combined-88.1/37.7/-80.5/42. **Approximate rectangles, not legal state polygon classifications**; border-area review required. Partition larger files locally and preserve provenance/versions. Conflicting duplicate identity rows in one extract fail atomically. Versioned imports preserve history; correlation collapses identical location copies while retaining differing candidates.

## FCC and OSM

FCC [ASR public access documentation](https://wireless.fcc.gov/uls/documentation/towintro-1.pdf) and [public notice](https://docs.fcc.gov/public/attachments/DA-00-46A1.pdf) establish downloadable registered structures, not carrier/technology occupancy. Review current source/data terms and raw field dictionaries with the operator. No raw ASR ZIP parser or current-update guarantee is claimed.

OpenStreetMap [copyright/licensing](https://www.openstreetmap.org/copyright) and [ODbL](https://wiki.openstreetmap.org/wiki/ODbL): retain attribution and comply with database/share-alike obligations for distributed derivatives. Telecommunications tags can be incomplete or user-observed; no carrier/technology inferred from a structure tag. No Overpass calls or proprietary sources.

For these sources the application accepts only an **operator-reviewed normalized CSV**:

```csv
identity,latitude,longitude,observed_at
synthetic-structure,41.1,-84.2,2026-11-01T00:00:00Z
```

Use source`fcc_asr_reviewed`/license`operator-reviewed-public-terms` only after actual terms/field/coordinate review; source`osm_reviewed`/license`ODbL-1.0` after applicable review. These labels record operator decisions, not automatic legal clearance. Include real source/version/date and attribution, identify transformation in notices; do not pretend this normalized format is a vendor API/file contract. FCC rows are `registered_structure`, OSM rows `mapped_structure`, technology/carrier unknown. Such structures never enter serving-cell matching or service qualification as cellular coverage.

Open data is advisory. Candidate distances/bearings are approximate to estimated coordinates. Stale180-day observations are marked; matching is exact technology/MCC/MNC/TAC/full-cell identity and can remain ambiguous. No particular carrier support, installation availability, capacity or speed is guaranteed.
