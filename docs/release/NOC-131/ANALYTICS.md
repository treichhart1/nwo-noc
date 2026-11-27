# Calculation and evidence contract

A month is `[first day00:00UTC, next first day00:00UTC)`. Each source-disconnected service appears at most once in monthly gross observed cancellations; duplicate request/history evidence does not add another loss. An initial disconnected source import (`previous_status=NULL`) is excluded. Missing imported reason stays unknown. Account/service membership uses existing canonical IDs.

For complete opening and closing source cohorts:

- Service churn = number of opening service IDs absent at close / opening service IDs.
- Account churn = number of opening customer IDs with no remaining services at close / opening customer IDs.
- New services/accounts = closing IDs absent at opening. Net growth = closing count − opening count.
- Empty or incomplete/unavailable opening counts produce unavailable rates. Intramonth installation/disconnection episodes are not the same measure as month-opening cohort attrition.

Verified completed signup installation work orders are reported separately from cohort additions. Service status snapshots alone cannot prove an installation occurred. Breakdown dimensions are package, tower, sector, town, county, reported reason, separate operator reason and normalized competitor. Unknown/missing mappings remain unknown; town/county in verified requests are customer-reported, not invented geocoding. Without historical request snapshots, current canonical association/pricing is labeled snapshot evidence, not verified historical billing. Voluntary/involuntary classification uses the current administrator-configured reason taxonomy; original reports and event history remain intact.

Lost MRR = sum of explicit per-service recurring snapshots, once per service, grouped by currency and contracted/estimated basis. Annualized snapshot = lost MRR ×12, not guaranteed future cash loss. No invoiced or collected revenue is inferred. Missing amount/currency is counted as missing, not silently valued at zero. New recurring revenue, net recurring change and service tenure require complete attested cohorts with explicit historical price/start-date evidence. Net recurring change = closing recurring snapshot − opening recurring snapshot, grouped by currency/basis; new recurring revenue includes only new service IDs. Do not combine currencies or treat prospective/current snapshots as collected revenue.

Retained MRR uses each selected retained service once and an actual customer-accepted offer plus source-observed active service. Latest accepted offer costs are counted once per case, separating monthly and one-time estimated costs. Complete fully allocated operating costs are not inferred from an offer. Estimated retention profitability remains unavailable when those costs are missing; tower profitability calls the existing canonical finance calculation for a matching tower scope and month, preserving shared-cost allocation and accounting assumptions. No automatic retirement/pricing recommendation exists.

Risk assessments cite customer-reported dissatisfaction, existing documented incident impact, existing collector alerts and recent independently reviewed competitor observations where present. They state freshness, limitations and uncertainty. Shared equipment events are not proof of a particular customer's downtime. Competitor offer observations are not address coverage or equivalent-package evidence. No sentiment, probability, measured RF values, unsupported start dates or validated statistical model is invented. Recommendations require human review.
