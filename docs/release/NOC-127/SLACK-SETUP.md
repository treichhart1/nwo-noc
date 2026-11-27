# Slack operator setup — disabled until separately authorized

## Official contracts reviewed

* [Incoming webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/)
* [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)
* [auth.test](https://docs.slack.dev/reference/methods/auth.test/)
* [Rate limits](https://docs.slack.dev/apis/web-api/rate-limits/)
* [Request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
* [Block actions](https://docs.slack.dev/reference/interaction-payloads/block_actions-payload/)
* [History](https://docs.slack.dev/reference/methods/conversations.history/) and [replies](https://docs.slack.dev/reference/methods/conversations.replies/)

Actual workspace installation, app approval, private-channel membership, OAuth consent and egress require the workspace/server operators. Nothing in this document authorizes them automatically.

1. Create a dedicated Slack app in the approved workspace. Record workspace `T…`, app `A…`, bot user `U…` and channel `C…`/`G…` identifiers. Use dedicated restricted operations/test channels. Review workspace data retention and personnel access. Customer PII and financial information are prohibited even in private channels.
2. Select **webhook** or **bot**. Webhooks need the incoming-webhook scope and one channel-bound URL per approved channel. They cannot override the assigned channel. Bot posting requires `chat:write`; invite it to each approved channel. Do not request `chat:write.public`, administrator, user-write or equipment permissions. Optional read-only reconciliation needs separately approved `channels:history` or `groups:history`, method eligibility and actual token/workspace permission testing. No unrestricted history pagination occurs.
3. Store webhook URLs/bot token and signing secret through the authenticated admin screen, never shell arguments, logs, screenshots or CI. The screen is write-only. Bot credential JSON has exactly `bot_token` and `signing_secret`. Webhook JSON has exactly `webhooks` (channel→URL) and `signing_secret`. URLs must use `https://hooks.slack.com/services/<configured-team>/B…/<secret>` without port/query/redirect. Tokens start `xoxb-`; no user tokens are supported. Use only disposable synthetic values in tests.
4. Prepare configuration JSON in the admin screen. Example contains identifiers only, not credentials:

```json
{"enabled":false,"mode":"bot","workspace_id":"T123456789","app_id":"A123456789","bot_user_id":"U123456789","approved_channels":["C123456789"],"test_channels":["C123456789"],"channels":{"default":"C123456789","critical":"C123456789","maintenance":"C123456789","rf":"C123456789","technician":"C123456789"},"routing":{"power_failure":"critical","rf_degradation":"rf","technician_assignment":"technician"},"dashboard_origin":"https://noc.example.invalid","registration_reference":"separate-operator-approval","minimum_severity":"warning","cooldown_seconds":300,"max_per_minute":20,"max_per_hour":200,"interactions_enabled":false,"history_enabled":false}
```

All five channel roles are required; event overrides select a role. Critical severity routes to the critical channel. Every routed destination must be in both approved and test channel lists; initial scope stays test-only until separate review explicitly expands that list. Lower-severity maintenance/assignment/signup events require the configured `info` threshold. Do not change privacy rules to expose customer signup fields.

5. Keep `SLACK_NATIVE_TRANSPORT_ENABLED=false`, `SLACK_NATIVE_WORKER_ENABLED=false`, `SLACK_EGRESS_APPROVED=false`, `SLACK_INTERACTION_GATEWAY_ENABLED=false` for isolated development. A separately authorized host may explicitly enable native transport/worker/egress and online mode; the existing legacy mock-only live flag remains prohibited. Native SMS and Slack can share the existing worker pool. Do not enable the legacy mock worker concurrently. Native Slack interval is bounded5–300s, default5s; provider backoff/budgets constrain actual sends. TLS verification and fixed Slack origins are mandatory. No redirects, environment proxy trust or insecure HTTP fallback.
6. After host, credential, workspace, channel and registration review, set configured enabled and independently authorize the provider in the admin screen. This still sends nothing. Preview an existing verified source, review the exact payload/fingerprint and explicitly release that event. Changing source evidence or provider revision invalidates the release. Never use actual release approval during development. Existing notification approval is not equivalent to native Slack release.

## Threads and receipts

Bot responses must include matching channel, timestamp, app and bot identity. The first accepted incident post establishes a root; subsequent updates/escalations/assignment changes/restoration/closure reply in that thread. An unresolved root blocks dependent replies. Per-source cooldown suppresses repeated updates while allowing severity escalation, assignment changes and recovery/closure transitions. Per-channel spacing, minute/hour budgets and bounded batch/attempt counts limit storms.

Webhook HTTP200 `ok` establishes submission acceptance but does **not** return a message timestamp. It cannot automatically establish a root or reconcile a missing receipt. It may reply only where an independently verified bot root already exists. Do not claim identical webhook and bot capabilities or exactly-once delivery. Slack acceptance is not proof a human read the message.

For a quarantined bot attempt, obtain the exact candidate Slack timestamp through separately authorized operator review; approve a bounded read-only lookup. The app verifies workspace/channel/bot/app/exact message text and attempt time window; no result or missing history scope never proves nonacceptance and never releases a resend. Webhook unknown acceptance remains quarantined for operator incident handling.

## Optional interactions

Use a separate authenticated callback deployment, not a publicly reachable private backend. Review `deploy/slack-interaction-gateway.compose.yml`, `deploy/slack-interaction-nginx.conf.example` and `deploy/slack-gateway-role.sql`. They are not part of automatic installation. Use the reviewed immutable backend image digest, restricted DB login with only the NOLOGIN gateway access role, a separate random gateway SECRET_KEY and only the `slack-callback` key. Never mount notification vault, private JWT key or full runtime.env. Configure required application settings with synthetic admin bootstrap values in the standalone environment; do not run administrator initialization there.

Set Slack Interactivity Request URL to the separately approved HTTPS `/callbacks/slack/interactions` endpoint. Map each Slack user to an active NOC admin/operator/technician and allowed channels. Unmapped users are denied; technicians require current incident assignment or a current site grant. Enable configured interactions and the separate gateway flag only after TLS/signature/replay/role tests. The receiver verifies Slack v0 signature against exact raw body, ±300s timestamp, app/workspace/channel/receipt/user ownership, 16KiB size and a durable replay key. It acknowledges within a bounded processing window and queues a verified event for the private worker. ACK/review buttons only acknowledge an incident or add a fixed technician-review note. They never close incidents, dispatch technicians, execute commands, write equipment, or approve SMS. The open-dashboard button still requires NOC login. No `response_url` is fetched.
