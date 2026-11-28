# NOC-135 addendum — Actual GUI screenshots and visual acceptance

Base: NOC-134 draft PR137, commit `690a206f75f63a2f8531557cd8cccb0f196661c3`, tree `fcc023cf46ec84337665bdcc8b052174a1e0c784`. Base workflows902/903 each passed all13 jobs. No earlier NOC-135 implementation/PR was present when this addendum began. This branch implements the requested actual-GUI evidence; it does not infer additional unspecified NOC-135 feature work.

## Reproducible capture

The existing isolated Ubuntu application acceptance launches the real built backend/frontend/PostgreSQL and seeds its existing400-device rural WISP fixture plus vendor RF/microwave, tower/customer/IPAM/inventory/technician/maintenance/signup/fiber/planning/finance/outage data. `scripts/gui/seed.py` adds bounded fictional cancellation and cellular observations only after checking offline staging, explicit synthetic enablement, existing400+ devices and entirely synthetic inventory. The real application services and APIs supply the screenshot data; no Playwright response fulfillment, replacement DOM, AI imagery, screenshot-specific CSS or fabricated basemap is used.

`frontend/tests/gui-preview.spec.ts` signs in through the real login form and captures the existing private screens. Technician capture uses the existing technician identity. Native SMS, Slack, email, Powercode and equipment integrations remain disabled. External map tile requests are blocked and recorded, so an offline basemap can be absent; actual markers/controls and unavailable labels remain visible. Screenshotting does not authorize external requests.

The existing disposable WordPress installations capture their actual installed plugin pages before test submissions/uninstall. The public status fixture is explicitly synthetic; signup/cancellation pages use the real restricted synthetic PostgreSQL gateway, TLS verification and installed plugin iframe. No live website changes occur.

All30 requested views are captured at1920×1080 and390×844, plus a separate SMTP configuration view: **62 captures /124 original PNGs**, each with both exact viewport and full-page versions. Full-page height depends on actual content. Configuration screenshots contain blank write-only credential fields, no passwords/tokens. Data are fictional and expressly labeled; competitor directory names do not assert current pricing or coverage.

The original13 CI jobs remain required. The existing aggregate release gate downloads the four screenshot parts from its own run and executes `scripts/gui/package.py`. The packager rejects missing/duplicate views, wrong PNG dimensions and unsafe paths. It retains layout findings and runtime errors, builds a searchable self-contained `index.html`, `manifest.json` with SHA256 for every PNG, a visual-review checklist, and `nwo-noc-gui-preview.zip`. It uploads the complete package as **nwo-noc-gui-preview** with30-day retention. Final PR ledger records exact source, run/job results, artifact URLs/checksum and executed visual findings. No inherited pass substitutes for executed captures.

## Review and approval

Download the Actions artifact, extract it, open `index.html`, then inspect original desktop/mobile viewport and full-page images. Check color/typography, sidebar hierarchy, readable charts, map controls, dashboard density, empty space, overlap, navigation, forms and mobile task flow. The manifest records horizontal overflow and runtime/API failures; successful PNG creation does not mean visual acceptance. Existing technical JSON configuration, separate module layouts, long administrative forms, missing evidence and offline basemap limitations must remain visible in the assessment.

**Operator visual approval is REQUIRED and pending before any actual installation.** This is review evidence, not installation authorization or production readiness. No physical operator server was accessed. Existing native firmware/field contracts, security/vendor risks, dataset/Redis/dependency licensing, signing, VPN/MFA, current malware definitions, physical offsite recovery and live-provider/equipment acceptance blockers remain unchanged. All PRs remain draft/open/unmerged.
