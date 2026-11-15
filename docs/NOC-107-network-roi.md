# NOC-107: Operational financial analytics

## Scope and dependency strategy

Branch `feature/noc-107-network-roi` starts at NOC-106 PR #110, commit `8829664bb23bde7fa3c07d309412b3bcde0af8a0`; CI #551 and #552 passed. That draft contains NOC-105 #109, NOC-104 #108 and the complete upstream inventory/customer/maintenance development chain. No ancestor was merged. Review/integrate ancestors in their recorded order, then #108, #109, #110, this PR; retarget and revalidate children after upstream integration. Do not apply this migration to an incomplete base.

This module provides operational network financial decision support inside the existing NOC. AI Fiber Designer continues to own fiber engineering budgets, construction cost estimates, 811/project finance and construction ROI. No construction project, design budget or fiber construction financial model is added. Operational fiber transport expenses and installed network equipment investments can be documented here without duplicating construction estimates.

## Data architecture

Migration `0026_network_finance` follows `0025_fiber_designer_sync`. It adds:

- `finance_periods`: immutable start/end/currency, explicit closing. End dates are exclusive; 1–366 days. Ledger periods cannot overlap within one currency, preventing accidental acquisition-cost duplication. No FX conversion is assumed.
- `finance_scopes`: financial reporting identities mapped to existing sites, sectors, links, unified edges/circuits, Powercode services, signup plans and equipment assets. Financial-only network/group scopes create no infrastructure or customers. Group membership uses existing service IDs.
- `finance_entries`: append-only Decimal facts with kind, category, basis, source identity, evidence, actor and timestamp. Native dimensions are captured at entry time so later customer moves do not rewrite historical revenue attribution.
- `finance_allocations` / `finance_shares`: one immutable allocation per original cost, captured method/weights/evidence, exact cent-valued shares. PostgreSQL deferred validation rejects an allocation whose shares do not equal the original expense.
- `finance_attestations`: append-only completeness declarations for each reporting scope and revenue/expense basis. Later attestations supersede earlier ones without overwriting history.
- `finance_recurring`: documented monthly expense templates. Materialization is manual, idempotent and restricted to exact calendar months fully covered by the template. Nonmonthly/prorated costs require explicit entry.
- `finance_assumptions` / `finance_scenarios`: operator inputs, evidence and immutable baseline/result snapshots. Business transport contract terms are typed assumptions linked to existing circuit scopes.
- `finance_imports`: previewed mock/native reconciliation with exact digest approval.
- `finance_reports`: immutable historical report snapshots with a content digest; unchanged financial inputs reuse an existing snapshot.

Foreign keys and period/basis/kind indexes support bounded queries. PostgreSQL protects fact, allocation, scope, assumption, scenario and report history against update/delete. Corrections append an exact reversal. A later open adjustment period can reverse a closed period's fact in the same currency; the original remains intact. Reversal allocation follows the original recorded shares.

No monetary values use floating-point arithmetic. Decimal amounts use two currency decimal places; this release targets currencies with two minor-unit places. Unsupported accounting currencies/FX policies require future work. This is an operational subledger, not a general ledger, tax system, billing platform or audited financial statement.

## Revenue evidence and Powercode

Revenue bases are separate lanes:

| Basis | Meaning |
| --- | --- |
| contracted | Documented recurring service obligation; not an invoice or receipt |
| invoiced | Documented billed charge; not collected cash |
| collected | Explicitly verified collection with evidence and acknowledgement |
| estimated | Manually supported estimate |

Reports select one lane and never sum contracted, invoiced and collected amounts together. A service can have corresponding observations in multiple lanes without being counted several times in one report. Recurring subscription/transport revenue is separate from one-time installation revenue. Internet access is not automatically attributed to a transport circuit or every upstream link.

The mock adapter accepts stable existing NOC-95 service external IDs, amount, currency, category, financial basis and evidence. It requires active services with the existing **`verified`** mapping state; inconsistent site/sector mappings, unknown/deleted services, wrong currency, duplicate facts and unverified collections are rejected. A matching explicit service financial scope is required. Plan/service/status/tower/sector information comes from the existing read-only customer foundation and is snapshotted with the financial fact. NOC-103 plan mappings are reused through their documented Powercode reference; catalog prices are not treated as billed revenue.

`POST /api/finance/v1/periods/{id}/imports/preview` changes no ledger entries. Review the source facts, captured dimensions and basis, then approve `/imports/{id}/approve` with the exact digest. Approval rechecks native facts and mappings. Changes invalidate the preview. Repeated identical facts are skipped; changed facts conflict and require explicit reversal/reconciliation. There is no production Powercode transport, credential or billing-write method in this module. Actual native recurring-charge API compatibility remains unverified.

## Expenses, investments and maintenance

Supported operating categories: lease, power, backhaul, upstream internet, licensing, equipment support, maintenance, insurance, software and other documented network expenses. Enter actual and estimated expenses separately; missing fields are not silently assigned zero. A recurring expense template documents a known contract/accrual or an estimate, not proof of payment. No external bill is generated.

Inventory reconciliation reads existing NOC-100 `Financial` and `CostEntry` records for explicitly selected assets. Purchase cost enters only its acquisition period, with a known purchase date and matching currency. Missing acquisition dates/prices remain unavailable. Purchase cost is a documented acquisition value, not proof of payment. Replacement estimates remain separate from capital paid/incurred and operating expenses. Maintenance cost entries reference existing maintenance IDs, retaining the NOC-102 equipment-service relationship; service records are not charged again separately. Missing labor rates, installation costs or parts prices are not invented.

Configured straight-line depreciation is a noncash **assumption**, kept separate from operating cash expenses. It requires recorded cost, purchase date and useful life, requires an explicit salvage value and reuses NOC-100 whole-calendar-month book-value calculations at the period boundaries, bounded by configured asset life. Missing salvage or other assumptions leave depreciation unavailable; accounting approval is required before accounting use. Unsupported depreciation methods are unavailable. Retirement state and configured asset-life fields remain inventory-owned; this module does not change lifecycle states.

Capital reporting is cumulative documented investment through the reporting period's end, using original location/allocation snapshots. It is not net book value, a cash-flow statement or a silently revalued current tower inventory. Asset transfers do not rewrite historical attribution. Use reviewed financial adjustments/allocation for ownership changes. Historical capital can be incomplete if earlier acquisitions have not been reconciled; the capital completeness attestation must reflect that limitation.

## Shared cost allocation

An original cost is counted once. Allocated shares replace its direct report contribution; they are not additional expenses. Network totals sum each share once; tower rollups include direct and sector-level portions once. Revenue is not automatically duplicated across physical backhaul paths.

Methods:

- Equal: equal positive weights.
- Customer count: distinct active customer IDs from verified current native service mappings, captured with supporting service IDs at allocation time.
- Revenue weighted: documented recurring revenue for the explicitly selected revenue basis and period.
- Capacity weighted: explicit positive operator-documented capacity weights; interface throughput alone is not RF capacity.
- Manual: explicit positive weights and an audit reason.

Weights are normalized by their sum. Largest-remainder allocation distributes indivisible cents, with scope ID as deterministic tiebreaker. The final shares exactly equal the original cost. Zero/unknown weights and duplicate destinations fail rather than inventing weights. The method, original source, weight evidence and timestamp remain visible in reports.

Allocation is one level. Choose the final reporting destinations (for example sectors) when creating the rule; tower rollup then follows captured dimensions. Group costs do not automatically expand into customer/sector allocations. Allocations cannot be edited: reverse the original expense and document a corrected fact/allocation. Explicit allocation is also required to attribute shared upstream internet to transport-only circuits; bandwidth equality is never assumed.

## Formulas and completeness

For the selected period/currency/revenue basis/expense basis:

- Gross revenue = sum of selected revenue facts, including installation revenue, net of exact reversals.
- Recurring revenue = selected subscription + transport revenue, excluding installation revenue.
- Operating expense = selected actual **or** estimated operating expense, net of reversals.
- Known contribution subtotal = gross revenue − known operating expense. This remains provisional when inputs are incomplete.
- Operating contribution = gross revenue − operating expense, available only after explicit completeness attestation.
- Operating margin = operating contribution / gross revenue × 100; unavailable if gross revenue is zero.
- Cumulative capital = documented capital facts through period end in the selected expense basis, net of reversals and using captured allocation.
- Operational period ROI = operating contribution / cumulative documented capital × 100. This is not annualized, an accounting total-return measure or guaranteed investment performance.
- Period months = period days × 12 / 365.2425.
- Modeled monthly recurring contribution = (recurring revenue − operating expense) / period months.
- Simple modeled payback months = cumulative capital / positive modeled monthly recurring contribution. Unavailable for zero/negative contribution, unknown capital, or incomplete inputs. Installation receipts are not assumed to recur.
- Revenue/customer = gross revenue / distinct customer IDs linked to documented selected revenue facts.
- Monthly ARPU = recurring revenue / period months / those documented customers.
- Cost/customer = complete operating expense / those documented customers.

The denominator is documented revenue-linked customers, not an invented customer count. The dashboard separately labels **current** verified active customer/service assignments; these current records are not historical customer snapshots. Financial snapshots preserve their capture time. Daily normalization approximates calendar months; exact invoice-month billing/proration is outside scope.

A scope/basis-specific attestation must explicitly cover revenue and all supported operating expense categories, including documented known-zero or not-applicable categories. Attesting only tower rent cannot make a tower profitable. Capital completeness is additionally required for ROI/payback; depreciation completeness is additionally required for contribution after noncash depreciation. The attestation is an accountable operator declaration, not an independently audited assurance. Technology-filtered slices do not inherit whole-scope completeness and remain provisional.

## Scenarios, recommendations and retirement

Scenarios preserve their baseline and supporting assumptions. Monthly proposed contribution = complete baseline monthly recurring contribution + revenue delta − expense delta. Incremental payback = new capital / positive monthly contribution delta. Sensitivity varies the revenue delta by the configured percentage; it is a simple assumption test, not a probability forecast. Incomplete baselines do not yield an asserted proposed total. Customer growth, prices and equipment changes are assumptions only.

Recommendations cite inputs, completeness and uncertainty. Negative complete contribution prompts financial review, not automatic retirement. High documented maintenance cost prompts investigation of history/options, not a predicted equipment failure. Typed, append-only per-period/scope screening policies configure maintenance share, backhaul/revenue share, low margin and low/high utilization. Documented defaults (25%, 30%, 20%, 10% and 85%) are advisory thresholds, not financial facts or industry guarantees. Missing capacity/demand does not justify underutilization or upgrade claims. Existing configured sector capacity is labeled separately from recorded utilization. Sector recommendations require three distinct recent supported NOC-98 samples within one hour, fresh within 15 minutes at the current/period boundary; missing or unsupported values never become zero. Such evidence indicates a recent investigation opportunity, not month-long peak demand or proven RF capacity.

Tower-retirement review includes verified current customer dependencies, documented backhaul links, the existing NOC-104 topology impact preview and its redundant-path uncertainty, operator-entered removal costs, contractual obligations and alternate-service evidence. Savings are not asserted without reviewed allocation/contract evidence. Qualified operator/engineering review is always required; no customers, devices, topology, work dispatches or purchases are changed.

## Dashboard and APIs

Open **Network ROI** in the authenticated NOC or `/?network-roi=home`. Select period, canonical scope, revenue basis, expense basis and optional service technology. API `totals` are recorded subtotals, not proof of complete financial inputs; use completeness, coverage, basis and source lines before interpreting them. The dashboard displays unavailable values when no corresponding fact/attestation exists. Rankings cover towers, sectors, backhaul, unified edges, circuits, plans, groups, assets and network scopes. Incomplete results remain unclassified. Network views provide recorded period revenue history; financial facts expose source and allocation method. Additional scopes can be paged in. API listing endpoints use bounded offsets and limits.

Reviewed workflows provide validated JSON examples for periods, scopes, facts, allocations, completeness, contracts, recurring expenses, source previews, scenarios, retirement review, reversals, snapshots and closing. Replace every example assumption with evidence. Review and approve import previews explicitly. Retry-safe operation keys are retained in memory after network errors; identical retries return the existing result. A page reload requires reloading records and checking history before starting a new mutation. No offline financial editing is claimed.

CSV export requires financial authorization and neutralizes spreadsheet formulas/control characters. Export includes financial source/evidence but does not add customer names, phone numbers, email addresses or billing credentials. Printable reports use the same protected report data. Scenario history contains immutable baseline comparisons.

## Security and operational safeguards

All `/api/finance/v1` routes, including exports and schema-changing operations, require existing NOC authentication and **administrator financial permission**, consistent with NOC-100 financial data. General operators/technicians/read-only users cannot read financial values. Granular delegated finance roles require future authorization work; no broad operator grant is added here. Request budgets, server-side validation, redacted validation errors, at most 1 MiB POST requests, no-store responses and mutation/audit receipts are reused.

The module stores no billing credential, customer payment card, personal identifier or external API password. Native customer associations are protected internal IDs. PostgreSQL/service bindings, private backup security and operator-managed encryption-at-rest requirements remain those of the existing isolated deployment. Do not publish this dashboard or financial CSV files. Authorized administrators are responsible for secure report handling and not entering secrets into evidence fields.

Writes reuse the existing inventory/field transaction locks and commit atomically. Source identifiers and per-period facts enforce duplicate prevention. Database triggers protect append-only records, period boundaries, collection acknowledgement and exact allocation totals. Financial changes modify only NOC financial tables and audit/receipt records. No new worker, public endpoint, live provider transport or external notification delivery is enabled.

## Validation, performance and recovery

Automated coverage includes revenue lanes, completeness, tower/sector/network totals, exact allocations for five methods, duplicate prevention, transport circuits, inventory/maintenance reconciliation, ROI/payback, scenarios, retirement safety, stale mapping previews, imports, ledger immutability, authorization, export redaction and dashboard filtering. Browser cases exercise mobile incomplete-data states, basis filtering, completeness, scenarios and reviewed mock import. All inherited NOC-95 through NOC-106 checks remain enabled in CI. Controlled out-of-order browser tests cover financial filter changes and work-order refreshes: an older response cannot overwrite the selected financial basis or erase a newer recorded inspection. These tests fail against the original refresh behavior and pass with request-order guards.

Rollback-only PostgreSQL benchmarks use 40 synthetic POPs and 500/2,000 financial entries, with actual row counts and conservation checks. The dashboard shares a single ledger load across its tower batch. Requests bound a report to 20,000 selected period/cumulative-capital facts; larger histories require archival/partitioning work. Source imports are bounded to 2,000 generated facts and 1 MiB previews. Stored report snapshots are reused by digest; dynamic reports are recalculated from current documented inputs. Measurements are not sustained/concurrent production sizing. Exact final counts, CI links and performance measurements are recorded in the draft PR.

Back up PostgreSQL and the existing private application configuration. No new binary file store is introduced. Restore into a new isolated database, verify Alembic head and financial facts/snapshots, and confirm no external integrations before startup. Empty-schema down/up is a migration rehearsal, not an operational rollback strategy. For financial corrections, use exact reversals and reviewed replacements; retain audit history. Never drop financial history or overwrite a production database to undo a report.

Known limitations: mock-only Powercode charges; no verified native charge/collection API compatibility; administrator-only delegated finance access; explicit allocation and completeness depend on operator review; average-month simple payback; operational rather than accounting ROI; no automatically inferred useful life or tax treatment; no construction budgets; current capacity observations/underutilization are not invented; sustained multi-user production load and accounting reconciliation require future acceptance. No production financial data was used and no business action was executed.
