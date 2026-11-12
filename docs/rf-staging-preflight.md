# NOC-74B — RF staging preparation

This is a **preparation-only** milestone. Nothing is deployed or enabled.

## Non-invasive configuration gate

On a separately provisioned staging host with a staging database, set
`APP_ENV=staging`, `RF_COLLECTION_ENABLED=false`,
`RF_HISTORY_PRUNING_ENABLED=false`, and `UISP_VERIFY_TLS=true`.
From the backend directory, run:

```bash
python scripts/rf_staging_preflight.py
```

The script reads environment variables only, prints JSON checks, and exits
nonzero if the configuration is unsafe for initial staging. It does not
contact UISP, access a database, change devices, or start workers.

## Additional manual gates before any deployment

- Obtain explicit operator approval and use an isolated staging host.
- Review stacked draft PRs and migrations in dependency order; do not merge automatically.
- Keep NOC dashboard/API VPN-only and authenticated.
- Use a disposable or backed-up staging PostgreSQL database and validate Alembic migrations.
- Use a read-only UISP token and verified TLS; do not use live credentials in CI.
- Verify baseline inventory sync and RF API authentication before considering RF opt-in.
- Document rollback: disable RF collection and pruning, restore approved app release if needed.
- Never execute radio configuration commands as part of RF staging.
