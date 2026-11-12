# NOC-75V — Isolated staging validation procedure

**Status:** Procedure only. No staging environment has been provisioned, tested, or approved. All draft PRs remain unmerged.

## Preconditions (stop if any fails)

1. Obtain operator approval for a dedicated nonproduction host or VM, isolated from the WISP management networks.
2. Do not reuse production PostgreSQL, Redis, signing keys, admin credentials, tokens, or backup files.
3. Use **synthetic** radio, customer, and site data; do not import real customer or operational secrets.
4. Block egress to production MikroTik, UISP, Powercode, billing, and other management endpoints using host/firewall controls. Document the rules and test them before application startup.
5. Disable RF collection, RF pruning, outbound alerts, automation, and scheduled integrations. Leave UISP token unset and use mock services.
6. Restrict inbound access to the operator's VPN or a private test network. Confirm backend/frontend port binding and no exposed database/Redis ports.
7. Prepare a disposable staging database and a snapshot/backup that can be restored independently. Never restore staging backups into production.

## Execution checklist

1. Review all draft PR diffs and ensure the intended branch includes the stacked changes. Record commit SHA and operator approval.
2. Generate **staging-only** secrets and environment configuration. Set `APP_ENV=staging`; keep `API_DOCS_ENABLED=false`, `UISP_VERIFY_TLS=true`, `RF_COLLECTION_ENABLED=false`, and `RF_HISTORY_PRUNING_ENABLED=false`.
3. On a **disposable staging copy**, run `docker compose config --format json` and validate its output using `python3 scripts/validate_compose_network.py <compose.json>`. The production environment preflight is intentionally not a staging approval tool.
4. Confirm the network isolation and outbound firewall deny rules with operator-approved nonproduction tests.
5. Start only the isolated database/Redis first. Take a staging backup, run migrations, verify schema, then perform a destructive restore drill **only on the disposable staging database**.
6. Start backend/frontend with all integrations mocked or disconnected. Verify authentication, admin/operator RF authorization, health endpoint, and unauthorized access rejection.
7. Execute automated unit/integration suites with disposable PostgreSQL, and perform manual UI checks against synthetic devices/sites.
8. Review service logs for secrets, unexpected outbound attempts, crashes, and access-control failures.
9. Shut down staging services; document the results and restore/recovery times. Confirm no production devices or customer data were touched.
10. Record independent evidence for each requirement in `docs/noc-75t-staging-evidence-template.json` **only after it is actually verified**. Run `python3 scripts/staging_readiness_gate.py <evidence.json>` on the reviewed evidence.

## Rollback and stop conditions

Immediately stop staging and preserve logs if: any unexpected production network traffic occurs, credentials leak, integration jobs start, ports become public, migrations fail, restore fails, or authorization tests fail. Revoke any staging credentials at risk, isolate the VM, and restore the **staging-only** snapshot after root-cause review. Never use this procedure to restart, reconfigure, or roll back production.

## Evidence required

Record operator, date, test VM identity (no credentials), branch SHA, isolation rules and test result, disposable database backup/restore outcome, migration result, RF-disabled confirmation, mocked UISP confirmation, authentication/RBAC tests, relevant CI run, failure/rollback drill, and unresolved issues.

**Gate:** Successful CI or offline audits do not constitute staging acceptance. Production deployment requires separate operator authorization and an independently reviewed deployment/rollback plan.
