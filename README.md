# NWO NOC

A modern Network Operations Center (NOC) platform designed for Wireless Internet Service Providers (WISPs).

## Overview

NWO NOC provides a centralized platform for monitoring, managing, and operating wireless broadband networks.

The project is being developed specifically for WISP operations with support for:

- Tower management
- POP management
- Backhaul monitoring
- Fiber infrastructure
- Customer equipment
- GIS mapping
- MikroTik integration
- UISP integration
- AI-assisted troubleshooting
- Inventory management
- Incident management
- Network topology visualization

---

## Current Features

- Device management
- Site management
- Backhaul management
- Sector management
- Live monitoring
- Alert dashboard
- Incident tracking
- Maintenance windows
- REST API
- Docker deployment

---

## Planned Features

### Operations

- Root cause analysis
- Customer impact engine
- Revenue impact calculations
- Dependency graph
- Intelligent alert suppression

### Integrations

- MikroTik RouterOS API
- UISP synchronization
- SNMP polling
- Syslog collection

### GIS

- Interactive tower map
- Fiber routes
- Microwave paths
- Customer mapping

### AI

- Automated troubleshooting
- Capacity forecasting
- Predictive maintenance
- Network recommendations

---

## Technology Stack

Backend

- Python
- FastAPI
- SQLAlchemy
- Alembic

Frontend

- React
- TypeScript
- Vite

Infrastructure

- Docker
- PostgreSQL
- Redis

---

## Installation

```bash
git clone <repository>

cd nwo-noc

docker compose up -d --build
```

---

## Project Status

Current Version

v3.2.1-rc.1

Current Sprint

Sprint 1 – Project Foundation

---

## License

Private Internal Project

Copyright © 2026 Tim Reichhart

All Rights Reserved.

For isolated Ubuntu 24.04 installation, automated release/browser acceptance,
backup recovery and rollback, see [NOC-84 installation and recovery](docs/NOC-84-installation-and-recovery.md).

Synthetic WISP operational validation, correlation rules and measured scale results:
[NOC-85 operational readiness](docs/NOC-85-operational-readiness.md).

Read-only UISP/RouterOS contracts, activation guards, secret handling and synthetic
validation: [NOC-86 integration readiness](docs/NOC-86-read-only-integrations.md).

Ubiquiti/Cambium RF collection contracts, SNMPv3 safety, historical diagnostics,
synthetic validation and real-device prerequisites:
[NOC-87 radio telemetry](docs/NOC-87-radio-telemetry.md).

Racom/Aviat carrier-level contracts, microwave history, hysteresis and backhaul
correlation: [NOC-88 microwave monitoring](docs/NOC-88-microwave-monitoring.md).

Historical comparisons, suspected interference, congestion, scores/confidence,
projection/retention and query limits:
[NOC-89 historical RF analytics](docs/NOC-89-historical-rf-analytics.md).

NOC-90 adds [evidence-based RF troubleshooting and human review](docs/NOC-90-rf-troubleshooting.md), with deterministic facts, safe checks, audited notes/lifecycle and disabled-by-default constrained local explanations. No equipment commands or remediation are executed.
