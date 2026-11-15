import React, { useEffect, useRef, useState } from "react";
import { request, getToken } from "./lib/api";
import "./field.css";
const api = (p: string, b?: any) =>
  request(
    "/analytics/v1" + p,
    b === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(b),
        },
  );
const mutation = () => ({
  operation_key: crypto.randomUUID(),
  reason: "Operator requested private recorded monitoring analytics",
});
const show = (v: any) => JSON.stringify(v, null, 2);
export default function NetworkAnalytics() {
  const [me, M] = useState<any>(),
    [catalog, C] = useState<any>(),
    [health, H] = useState<any>(),
    [cfg, F] = useState<any>(),
    [report, R] = useState<any>(),
    [context, X] = useState<any>(),
    [error, E] = useState(""),
    [busy, B] = useState(false),
    [preset, P] = useState("24h"),
    [metric, K] = useState("rssi_dbm"),
    [site, S] = useState(""),
    [device, D] = useState(""),
    [uid, U] = useState("noc-network"),
    [start, ST] = useState(""),
    [end, EN] = useState(""),
    [link, L] = useState<any>(),
    [output, O] = useState("");
  const generation = useRef(0);
  const query = () => ({
    preset,
    metric,
    site_id: site ? Number(site) : null,
    device_id: device ? Number(device) : null,
    group: device ? "device" : "site",
    ...(preset === "custom"
      ? {
          start: new Date(start).toISOString(),
          end: new Date(end).toISOString(),
        }
      : {}),
  });
  async function refresh() {
    const [m, c, h, f] = await Promise.all([
      api("/me"),
      api("/catalog"),
      api("/health"),
      api("/configuration"),
    ]);
    M(m);
    C(c);
    H(h);
    F(f);
  }
  useEffect(() => {
    refresh().catch((e) => E(e.message));
  }, []);
  useEffect(() => {
    generation.current++;
    R(undefined);
    X(undefined);
    L(undefined);
  }, [preset, metric, site, device, start, end]);
  async function act(fn: () => Promise<void>) {
    B(true);
    E("");
    try {
      await fn();
    } catch (e) {
      E((e as Error).message);
    } finally {
      B(false);
    }
  }
  async function load() {
    const g = ++generation.current;
    const [r, c] = await Promise.all([
      api("/report", query()),
      api(
        "/context?" +
          new URLSearchParams({
            ...(site ? { site_id: site } : {}),
            ...(device ? { device_id: device } : {}),
          }),
      ),
    ]);
    if (g === generation.current) {
      R(r);
      X(c);
    }
  }
  async function csv() {
    const r = await fetch("/api/analytics/v1/export", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${getToken()}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(query()),
    });
    if (!r.ok)
      throw new Error(
        "Authorized export unavailable; narrow the range or retry",
      );
    download(await r.text(), "noc-recorded-analytics.csv", "text/csv");
  }
  function download(text: string, name: string, type = "application/json") {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const points = report?.series?.points || [],
    values = points.filter((p: any) => p.value != null),
    lo = Math.min(...values.map((p: any) => p.value)),
    hi = Math.max(...values.map((p: any) => p.value));
  return (
    <main className="fieldPage">
      <header>
        <a href="/">← NOC</a>
        <h1>Network Analytics</h1>
        <p>
          Recorded monitoring evidence • private Grafana visualization • no
          additional device polling
        </p>
      </header>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!me ? (
        <p>Loading authorized analytics…</p>
      ) : (
        <>
          <section className="card">
            <h2>Integration health</h2>
            <p data-testid="analytics-health">
              Grafana: {cfg?.enabled ? "enabled" : "disabled"} · Live
              connectivity:{" "}
              {health?.grafana_live_verified
                ? "recently verified"
                : "unverified"}{" "}
              · Metrics: {health?.metrics_state || "unavailable"}
            </p>
            <p>
              Last recorded telemetry: {health?.metrics_latest || "Unavailable"}{" "}
              · Queue backlog: {health?.queue_backlog ?? "Unavailable"} · Stale
              workers: {health?.stale_workers ?? "Unavailable"}
            </p>
            <p>
              Last check:{" "}
              {health?.last_check
                ? `${health.last_check.mode} / ${health.last_check.status}`
                : "Not checked"}
              . A mock check does not verify a running Grafana server.
            </p>
            <button disabled={busy} onClick={() => act(refresh)}>
              Refresh integration health
            </button>
          </section>
          <section className="card">
            <h2>Historical monitoring report</h2>
            <div className="analyticsFilters">
              <label>
                Period
                <select
                  aria-label="Period"
                  value={preset}
                  onChange={(e) => P(e.target.value)}
                >
                  {["1h", "24h", "7d", "30d", "90d", "custom"].map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </label>
              {preset === "custom" && (
                <>
                  <label>
                    Start (local time)
                    <input
                      type="datetime-local"
                      value={start}
                      onChange={(e) => ST(e.target.value)}
                    />
                  </label>
                  <label>
                    End (local time)
                    <input
                      type="datetime-local"
                      value={end}
                      onChange={(e) => EN(e.target.value)}
                    />
                  </label>
                </>
              )}
              <label>
                Metric
                <select
                  aria-label="Metric"
                  value={metric}
                  onChange={(e) => K(e.target.value)}
                >
                  {Object.entries(catalog?.metrics || {}).map(([k, v]: any) => (
                    <option key={k} value={k}>
                      {k} ({v.unit})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Tower / POP identifier
                <input
                  aria-label="Tower identifier"
                  type="number"
                  min="1"
                  value={site}
                  onChange={(e) => S(e.target.value)}
                />
              </label>
              <label>
                Equipment monitoring identifier
                <input
                  aria-label="Device identifier"
                  type="number"
                  min="1"
                  value={device}
                  onChange={(e) => D(e.target.value)}
                />
              </label>
            </div>
            <button disabled={busy} onClick={() => act(load)}>
              Load recorded report
            </button>{" "}
            <button disabled={busy || !report} onClick={() => act(csv)}>
              Export authorized CSV
            </button>{" "}
            <button disabled={!report} onClick={() => window.print()}>
              Print report
            </button>
            {report && (
              <>
                <p data-testid="analytics-range">
                  {report.series.start} — {report.series.end} ·{" "}
                  {report.series.storage} · {report.series.unit}
                </p>
                <p>
                  {report.series.warning ||
                    "Missing measurements are unavailable. Sample averages are not an availability SLA."}
                </p>
                {values.length > 0 ? (
                  <svg
                    role="img"
                    aria-label="Recorded measurement trend"
                    viewBox="0 0 600 160"
                    style={{
                      width: "100%",
                      maxWidth: 900,
                      background: "#101d30",
                    }}
                  >
                    {[...new Set(points.map((p: any) => p.scope_id))].map(
                      (id: any, i) => {
                        const group = points.filter(
                          (p: any) => p.scope_id === id,
                        );
                        const t0 = Date.parse(report.series.start),
                          duration = Date.parse(report.series.end) - t0;
                        return (
                          <g key={id}>
                            {group.map((p: any, n: number) => {
                              const prev = group[n - 1];
                              if (
                                !prev ||
                                p.value == null ||
                                prev.value == null ||
                                Date.parse(p.time) - Date.parse(prev.time) >
                                  report.series.bucket_seconds * 1500
                              )
                                return null;
                              return (
                                <line
                                  key={n}
                                  x1={
                                    10 +
                                    (580 * (Date.parse(prev.time) - t0)) /
                                      duration
                                  }
                                  x2={
                                    10 +
                                    (580 * (Date.parse(p.time) - t0)) / duration
                                  }
                                  y1={
                                    145 -
                                    (130 * (prev.value - lo)) / (hi - lo || 1)
                                  }
                                  y2={
                                    145 -
                                    (130 * (p.value - lo)) / (hi - lo || 1)
                                  }
                                  stroke={
                                    ["#44c9ba", "#edb660", "#a7a2ff"][i % 3]
                                  }
                                  strokeWidth="2"
                                />
                              );
                            })}
                          </g>
                        );
                      },
                    )}
                  </svg>
                ) : (
                  <p>
                    No valid measurements in this scope and time range.
                    Unavailable does not mean healthy.
                  </p>
                )}
                <div style={{ overflowX: "auto" }}>
                  <table>
                    <thead>
                      <tr>
                        {[
                          "Time (UTC)",
                          "Scope",
                          "Recorded mean",
                          "Min",
                          "Max",
                          "Valid",
                          "Missing",
                        ].map((x) => (
                          <th key={x}>{x}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {points.slice(-240).map((p: any, i: number) => (
                        <tr key={i}>
                          <td>{p.time}</td>
                          <td>{p.scope_id}</td>
                          <td>{p.value ?? "Unavailable"}</td>
                          <td>{p.minimum ?? "Unavailable"}</td>
                          <td>{p.maximum ?? "Unavailable"}</td>
                          <td>{p.valid_count}</td>
                          <td>{p.missing_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p>
                  Display limited to the latest 240 report points. Export
                  contains all bounded report rows.
                </p>
                <h3>Outage history</h3>
                <p>
                  {report.outages.count} authoritative incidents in range.
                  Durations are clipped to the selected period; customer impact
                  is a recorded estimate.
                </p>
                <pre>{show(report.outages.items)}</pre>
                <h3>Evidence-based summaries</h3>
                {report.recommendations.length ? (
                  report.recommendations.map((r: any, i: number) => (
                    <details key={i}>
                      <summary>{r.action}</summary>
                      <pre>{show(r.evidence)}</pre>
                      <p>{r.uncertainty}</p>
                    </details>
                  ))
                ) : (
                  <p>Insufficient repeated evidence for a recommendation.</p>
                )}
                <h3>Related NOC records</h3>
                <details>
                  <summary>
                    Equipment, IPAM, topology, maintenance and planning
                    comparisons
                  </summary>
                  <pre>{show(context)}</pre>
                </details>
                {context?.profitability_url && (
                  <a href={context.profitability_url}>
                    Authorized network profitability
                  </a>
                )}
              </>
            )}
          </section>
          <section className="card">
            <h2>Approved Grafana dashboards</h2>
            <p>
              Uses your private Grafana session. NOC credentials are never
              forwarded. VPN access and separate Grafana authorization may be
              required.
            </p>
            <label>
              Dashboard
              <select
                value={uid}
                onChange={(e) => {
                  U(e.target.value);
                  L(undefined);
                }}
              >
                {catalog?.dashboards.map((d: any) => (
                  <option key={d.uid} value={d.uid}>
                    {d.title}
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={busy || !cfg?.enabled}
              onClick={() =>
                act(async () => {
                  L(
                    await api("/dashboard-link", {
                      ...mutation(),
                      dashboard_uid: uid,
                      query: query(),
                      embed: false,
                    }),
                  );
                })
              }
            >
              Prepare secure dashboard link
            </button>
            {link && (
              <p>
                <a href={link.url} target="_blank" rel="noopener noreferrer">
                  Open approved private Grafana dashboard
                </a>
              </p>
            )}
            <p>
              Embedding remains disabled until authentication proxy, CSP and
              browser behavior have been reviewed and tested.
            </p>
          </section>
          {me.role === "admin" && cfg && (
            <section className="card">
              <h2>Administrator integration setup</h2>
              <label>
                <input
                  type="checkbox"
                  checked={cfg.enabled}
                  onChange={(e) => F({ ...cfg, enabled: e.target.checked })}
                />{" "}
                Enable approved dashboard links
              </label>
              <label>
                Private HTTPS Grafana URL
                <input
                  aria-label="Grafana server URL"
                  value={cfg.server_url}
                  onChange={(e) => F({ ...cfg, server_url: e.target.value })}
                />
              </label>
              <p>
                The server origin must also be approved in
                GRAFANA_ALLOWED_ORIGINS. Service credentials are private files
                configured on the server.
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const {
                      revision,
                      enabled,
                      server_url,
                      auth_mode,
                      embed_enabled,
                      dashboards,
                      export_device_ids,
                      export_metrics,
                      retention_days,
                      stale_seconds,
                    } = cfg;
                    await api("/configuration", {
                      ...mutation(),
                      expected_revision: revision,
                      enabled,
                      server_url,
                      auth_mode,
                      embed_enabled,
                      dashboards,
                      export_device_ids,
                      export_metrics,
                      retention_days,
                      stale_seconds,
                    });
                    await refresh();
                  })
                }
              >
                Save revision-checked configuration
              </button>{" "}
              <button
                disabled={busy || !me.mock_health_available}
                onClick={() =>
                  act(async () => {
                    O(
                      show(
                        await api("/health", { ...mutation(), mode: "mock" }),
                      ),
                    );
                    await refresh();
                  })
                }
              >
                Check synthetic Grafana fixture
              </button>{" "}
              <button
                disabled={busy || !me.live_health_enabled}
                onClick={() =>
                  act(async () => {
                    O(
                      show(
                        await api("/health", { ...mutation(), mode: "live" }),
                      ),
                    );
                    await refresh();
                  })
                }
              >
                Check approved private Grafana
              </button>{" "}
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const r = await api("/provision", mutation());
                    download(show(r.bundle), "noc-grafana-provisioning.json");
                    O(
                      `Provisioning exported only. Digest: ${r.digest}. No remote writes.`,
                    );
                  })
                }
              >
                Export provisioning templates
              </button>{" "}
              <button
                disabled={busy || !report}
                onClick={() =>
                  act(async () => {
                    const r = await api("/reports", {
                      ...mutation(),
                      query: query(),
                    });
                    O(`Immutable report saved: ${r.id}`);
                  })
                }
              >
                Save report snapshot
              </button>
              {output && <pre data-testid="analytics-output">{output}</pre>}
            </section>
          )}
        </>
      )}
      <style>{`.analyticsFilters{display:flex;flex-wrap:wrap;gap:1rem}.analyticsFilters label{min-width:180px}pre{max-height:24rem;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}@media print{button,select,input,header a{display:none}.card{break-inside:avoid}body{background:white;color:black}}`}</style>
    </main>
  );
}
