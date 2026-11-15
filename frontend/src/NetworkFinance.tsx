import React, { useEffect, useState } from "react";
import { request } from "./lib/api";
import "./field.css";
import "./finance.css";
const api = (p: string, b?: any) =>
  request(
    "/finance/v1" + p,
    b
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(b),
        }
      : {},
  );
const pretty = (v: any) => JSON.stringify(v, null, 2);
const money = (v: any, c = "USD") =>
  v == null
    ? "Unavailable"
    : new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: c,
      }).format(Number(v));
export default function NetworkFinance() {
  const [periods, P] = useState<any[]>([]),
    [scopes, S] = useState<any[]>([]),
    [pid, PID] = useState(""),
    [sid, SID] = useState(""),
    [rb, RB] = useState("contracted"),
    [eb, EB] = useState("actual"),
    [kind, K] = useState("tower"),
    [technology, T] = useState(""),
    [report, R] = useState<any>(),
    [rows, ROWS] = useState<any[]>([]),
    [history, H] = useState<any[]>([]),
    [batch, B] = useState<any>(),
    [review, V] = useState(false),
    [message, M] = useState(""),
    [busy, BUSY] = useState(false),
    [allowed, A] = useState(false),
    [pending, PENDING] = useState<any>(),
    [action, ACTION] = useState(""),
    [json, J] = useState(""),
    [output, O] = useState<any>(),
    [trends, TR] = useState<any[]>([]);
  async function load() {
    const [p, s, h] = await Promise.all([
      api("/periods"),
      api("/scopes"),
      api("/scenarios"),
    ]);
    P(p.items);
    S(s.items);
    H(h.items);
    if (!pid && p.items.length) PID(String(p.items[0].id));
    if (!sid && s.items.length) SID(String(s.items[0].id));
  }
  async function refresh() {
    if (!pid || !sid) return;
    const [r, d] = await Promise.all([
      api(
        `/periods/${pid}/report?scope_id=${sid}&revenue_basis=${rb}&expense_basis=${eb}${technology ? "&service_type=" + technology : ""}`,
      ),
      api(
        `/periods/${pid}/dashboard?kind=${kind}&revenue_basis=${rb}&expense_basis=${eb}`,
      ),
    ]);
    R(r);
    ROWS(d.items);
    TR(d.period_trends);
  }
  useEffect(() => {
    api("/me")
      .then(() => {
        A(true);
        return load();
      })
      .catch((e) => M(e.message));
  }, []);
  useEffect(() => {
    if (allowed) refresh().catch((e) => M(e.message));
  }, [pid, sid, rb, eb, kind, technology, allowed]);
  async function perform(path: string, body: any) {
    BUSY(true);
    M("");
    let op = pending;
    if (!op || op.path !== path || op.input !== pretty(body)) {
      op = {
        path,
        input: pretty(body),
        body: {
          ...body,
          operation_key: crypto.randomUUID(),
          reason:
            body.reason || "Financial administrator reviewed operational finance documentation",
        },
      };
      PENDING(op);
    }
    try {
      const out = await api(path, op.body);
      PENDING(undefined);
      M("Saved internal financial record; no external action performed");
      await load();
      await refresh();
      return out;
    } catch (e) {
      M((e as Error).message);
      return undefined;
    } finally {
      BUSY(false);
    }
  }
  function example(a: string) {
    ACTION(a);
    O(undefined);
    const id = Number(sid) || 1;
    const ex: any = {
      period: {
        code: "2026-01",
        starts_on: "2026-01-01",
        ends_on: "2026-02-01",
        currency: "USD",
      },
      scope: {
        code: "tower-existing",
        name: "Existing tower",
        kind: "tower",
        reference_id: 1,
      },
      fact: {
        scope_id: id,
        kind: "revenue",
        category: "subscription",
        basis: "contracted",
        amount: "0.00",
        external_key: "REPLACE-SOURCE-REFERENCE",
        evidence: "Replace with verified source or estimate evidence",
        verified_collection: false,
      },
      allocation: {
        entry_id: 1,
        method: "equal",
        targets: [{ scope_id: id }],
        revenue_basis: rb,
      },
      coverage: {
        scope_id: id,
        revenue_basis: rb,
        expense_basis: eb,
        categories: [
          "revenue",
          "lease",
          "power",
          "backhaul",
          "upstream",
          "license",
          "equipment_support",
          "maintenance",
          "insurance",
          "software",
          "other",
          "capital",
        ],
        evidence:
          "Replace with completeness evidence, including known-zero or not-applicable categories",
      },
      scenario: {
        scope_id: id,
        name: "Operational sector upgrade",
        revenue_basis: rb,
        expense_basis: eb,
        monthly_revenue_delta: "100.00",
        monthly_opex_delta: "20.00",
        capex: "1000.00",
        additional_customers: 2,
        sensitivity_percent: 10,
        evidence:
          "Replace with supported assumptions; no automatic pricing or equipment changes",
      },
      recurring: {
        scope_id: id,
        code: "monthly-power-estimate",
        category: "power",
        amount: "0.00",
        currency: "USD",
        basis: "estimated",
        starts_on: "2026-01-01",
        evidence: "Replace with documented monthly expense estimate",
      },
      powercode: {
        provider: "powercode_mock",
        charges: [
          {
            external_key: "synthetic-charge-1",
            service_external_id: "REPLACE-EXISTING-SERVICE",
            amount: "0.00",
            basis: "contracted",
            currency: "USD",
            evidence: "Replace with approved synthetic charge source evidence",
          },
        ],
      },
      inventory: { provider: "inventory", asset_ids: [1] },
      retirement: {
        scope_id: id,
        revenue_basis: rb,
        expense_basis: eb,
        removal_cost: null,
        contractual_obligations: null,
        alternate_service_evidence: null,
      },
      reverse: { entry_id: 1, adjustment_period_id: null },
      materialize: {},
      snapshot: {
        scope_id: id,
        revenue_basis: rb,
        expense_basis: eb,
        categories: [],
        evidence: "Reviewed historical financial report snapshot",
      },
      contract: {
        scope_id: id,
        name: "Business transport contract term",
        starts_on: "2026-01-01",
        ends_on: "2027-01-01",
        evidence: "Replace with documented operational contract terms",
      },
      policy: {
        scope_id: id,
        maintenance_share_percent: 25,
        backhaul_revenue_percent: 30,
        low_margin_percent: 20,
        low_utilization_percent: 10,
        high_utilization_percent: 85,
        evidence:
          "Replace with reviewed operational financial screening thresholds",
      },
      close: {},
    };
    J(pretty(ex[a] || {}));
  }
  async function submit() {
    try {
      const b = JSON.parse(json);
      const paths: any = {
        period: "/periods",
        scope: "/scopes",
        policy: `/periods/${pid}/policy`,
        contract: `/periods/${pid}/contracts`,
        fact: `/periods/${pid}/entries`,
        coverage: `/periods/${pid}/coverage`,
        scenario: `/periods/${pid}/scenarios`,
        recurring: "/recurring",
        materialize: `/periods/${pid}/recurring`,
        snapshot: `/periods/${pid}/reports`,
        close: `/periods/${pid}/close`,
        retirement: `/periods/${pid}/retirement-review`,
        powercode: `/periods/${pid}/imports/preview`,
        inventory: `/periods/${pid}/imports/preview`,
      };
      let path = paths[action];
      if (action === "allocation" || action === "reverse") {
        path = `/entries/${b.entry_id}/${action === "allocation" ? "allocation" : "reverse"}`;
        delete b.entry_id;
      }
      const out = await perform(path, b);
      O(out);
      if (action === "powercode" || action === "inventory") {
        B(out);
        V(false);
      }
    } catch (e) {
      M((e as Error).message);
    }
  }
  const recorded = (r: any, k: string) =>
    r.lines.some((l: any) => l.kind === k) ||
    (r.coverage_attestation?.categories || []).includes(
      k === "capex" ? "capital" : k === "opex" ? "__all_opex__" : k,
    )
      ? r.totals[k]
      : null;
  const select = (
    label: string,
    value: string,
    set: (s: string) => void,
    options: any[],
  ) => (
    <label>
      {label}
      <select
        aria-label={label}
        value={value}
        onChange={(e) => set(e.target.value)}
      >
        {options.map(([v, n]) => (
          <option value={v} key={v}>
            {n}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <main className="finance-page">
      <a href="/">← NOC dashboard</a>
      <h1>Network profitability</h1>
      <p>
        Private operational finance · no billing writes or fiber construction
        budgets
      </p>
      {message && <p role="status">{message}</p>}
      {!allowed ? (
        <p>
          Financial administrator permission required. No financial values
          loaded.
        </p>
      ) : (
        <>
          <section className="field-card" aria-label="Financial filters">
            {select("Reporting period", pid, PID, [
              ["", "Select period"],
              ...periods.map((p) => [
                String(p.id),
                `${p.code} · ${p.currency}${p.closed ? " · Closed" : ""}`,
              ]),
            ])}
            {select("Financial scope", sid, SID, [
              ["", "Select scope"],
              ...scopes.map((s) => [
                String(s.id),
                `${s.name} · ${s.kind} #${s.id}`,
              ]),
            ])}
            {select(
              "Revenue basis",
              rb,
              RB,
              ["contracted", "invoiced", "collected", "estimated"].map((x) => [
                x,
                x,
              ]),
            )}
            {select(
              "Expense basis",
              eb,
              EB,
              ["actual", "estimated"].map((x) => [x, x]),
            )}
            {select(
              "Service technology",
              technology,
              T,
              ["", "wireless", "fiber", "hybrid", "transport"].map((x) => [
                x,
                x || "All documented services",
              ]),
            )}
            {select(
              "Ranking category",
              kind,
              K,
              [
                "tower",
                "sector",
                "backhaul",
                "edge",
                "circuit",
                "network",
                "plan",
                "group",
                "asset",
              ].map((x) => [x, x]),
            )}
            <button
              onClick={async () => {
                try {
                  const next = await api(`/scopes?offset=${scopes.length}`);
                  S([...scopes, ...next.items]);
                } catch (e) {
                  M((e as Error).message);
                }
              }}
            >
              Load more financial scopes
            </button>
          </section>
          {report && (
            <section aria-label="Financial report" className="field-card">
              <h2>
                {report.scope.name} · {report.period.code}
              </h2>
              <p>
                {report.basis_label}. Expenses: {report.expense_basis}.{" "}
                {report.complete
                  ? "Completeness explicitly attested"
                  : "Incomplete inputs — profitability unavailable"}
              </p>
              <dl>
                {[
                  ["Recorded gross revenue", recorded(report, "revenue")],
                  [
                    "Recorded recurring revenue in period",
                    recorded(report, "revenue") == null
                      ? null
                      : report.totals.recurring_revenue,
                  ],
                  [
                    "Recorded operating expense subtotal",
                    report.complete
                      ? report.totals.opex
                      : recorded(report, "opex"),
                  ],
                  ["Operating contribution", report.contribution],
                  ["Cumulative documented capital", recorded(report, "capex")],
                  [
                    "Noncash depreciation assumption",
                    recorded(report, "depreciation"),
                  ],
                  [
                    "Replacement estimate",
                    recorded(report, "replacement_estimate"),
                  ],
                  [
                    "Revenue per documented customer",
                    report.revenue_per_customer,
                  ],
                  ["Monthly ARPU", report.monthly_arpu],
                ].map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{money(v, report.period.currency)}</dd>
                  </div>
                ))}
              </dl>
              <p>
                Documented revenue customers:{" "}
                {report.documented_revenue_customer_count} · ROI:{" "}
                {report.operational_roi_percent ?? "Unavailable"}
                {report.operational_roi_percent ? "%" : ""} · Modeled payback:{" "}
                {report.payback_months ?? "Unavailable"} months
              </p>
              <p>
                Current assignment context:{" "}
                {report.current_assignment_context?.known
                  ? `${report.current_assignment_context.active_customers} active customers / ${report.current_assignment_context.active_services} active services`
                  : "Unavailable"}{" "}
                (current records; not a historical customer snapshot)
              </p>
              <p>
                Missing inputs:{" "}
                {report.missing_inputs.join(", ") ||
                  "None in selected attestation"}
              </p>
              <p>{report.capital_basis}</p>
              {report.recommendations.map((a: any, i: number) => (
                <article key={i}>
                  <b>{a.action}</b>
                  <p>{a.uncertainty}</p>
                  <pre>{pretty(a.evidence)}</pre>
                </article>
              ))}
              {report.capacity_context && (
                <pre>{pretty(report.capacity_context)}</pre>
              )}
              <button onClick={() => window.print()}>
                Print financial report
              </button>{" "}
              <button
                onClick={async () => {
                  try {
                    const res = await fetch(
                      `/api/finance/v1/periods/${pid}/export?scope_id=${sid}&revenue_basis=${rb}&expense_basis=${eb}`,
                      {
                        headers: {
                          Authorization:
                            "Bearer " + localStorage.getItem("nwo_token"),
                        },
                      },
                    );
                    if (!res.ok) throw Error("Authorized export unavailable");
                    const url = URL.createObjectURL(await res.blob());
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = "network-finance.csv";
                    a.click();
                    URL.revokeObjectURL(url);
                  } catch (e) {
                    M((e as Error).message);
                  }
                }}
              >
                Export authorized CSV
              </button>
              <details>
                <summary>
                  Financial facts and allocation evidence ({report.lines.length}
                  )
                </summary>
                <div style={{ overflowX: "auto" }}>
                  <table>
                    <thead>
                      <tr>
                        {[
                          "ID",
                          "Kind",
                          "Category",
                          "Basis",
                          "Amount",
                          "Allocation",
                          "Source",
                        ].map((x) => (
                          <th key={x}>{x}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {report.lines.map((l: any, i: number) => (
                        <tr key={i}>
                          <td>{l.entry_id}</td>
                          <td>{l.kind}</td>
                          <td>{l.category}</td>
                          <td>{l.basis}</td>
                          <td>{money(l.amount, report.period.currency)}</td>
                          <td>{l.allocation_method}</td>
                          <td>{l.source_type}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </section>
          )}
          <section aria-label="Profitability rankings" className="field-card">
            <h2>{kind} financial comparison</h2>
            <p>
              Complete contribution ranked first; missing inputs remain
              unclassified.
            </p>
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>Scope</th>
                    <th>Revenue</th>
                    <th>Expenses</th>
                    <th>Contribution</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.scope.id}>
                      <td>
                        <button onClick={() => SID(String(r.scope.id))}>
                          {r.scope.name}
                        </button>
                      </td>
                      <td>{money(r.totals.revenue, r.period.currency)}</td>
                      <td>{money(r.totals.opex, r.period.currency)}</td>
                      <td>{money(r.contribution, r.period.currency)}</td>
                      <td>{r.complete ? "Attested" : "Incomplete"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {trends.length > 0 && (
              <div aria-label="Revenue history">
                {trends.map((t: any, i: number) => (
                  <p key={i}>
                    {t.period.code}: {money(t.revenue, t.period.currency)}
                    <meter
                      min="0"
                      max={Math.max(...trends.map((t) => Number(t.revenue)), 1)}
                      value={Number(t.revenue)}
                    />
                  </p>
                ))}
              </div>
            )}
          </section>
          <section className="field-card">
            <h2>Reviewed financial workflows</h2>
            <p>
              Map existing NOC records and document financial facts. Replace
              example values and review all assumptions. Capacity weights
              require documented evidence.
            </p>
            {select("Workflow", action, example, [
              ["", "Choose workflow"],
              ...[
                "period",
                "scope",
                "policy",
                "contract",
                "fact",
                "allocation",
                "coverage",
                "scenario",
                "recurring",
                "materialize",
                "powercode",
                "inventory",
                "retirement",
                "reverse",
                "snapshot",
                "close",
              ].map((x) => [x, x]),
            ])}
            <label>
              Financial workflow JSON
              <textarea
                aria-label="Financial workflow JSON"
                rows={12}
                value={json}
                onChange={(e) => J(e.target.value)}
                spellCheck={false}
              />
            </label>
            <button disabled={busy || !action} onClick={submit}>
              Submit reviewed workflow
            </button>
            {output && (
              <details open>
                <summary>Workflow result</summary>
                <pre>{pretty(output)}</pre>
              </details>
            )}
            {batch && (
              <section aria-label="Financial import preview">
                <h3>Import preview #{batch.id}</h3>
                <pre>{pretty(batch.report)}</pre>
                <label>
                  <input
                    type="checkbox"
                    checked={review}
                    onChange={(e) => V(e.target.checked)}
                  />
                  I reviewed source facts, mappings and financial basis
                </label>
                <button
                  disabled={busy || !review || batch.approved}
                  onClick={async () => {
                    const out = await perform(`/imports/${batch.id}/approve`, {
                      digest: batch.digest,
                    });
                    if (out) B(out);
                  }}
                >
                  Approve exact financial import
                </button>
              </section>
            )}
          </section>
          <section aria-label="ROI scenarios" className="field-card">
            <h2>Scenario comparisons</h2>
            <p>
              Immutable baseline and operator assumptions; no execution or
              billing effect.
            </p>
            {history.map((h) => (
              <details key={h.id}>
                <summary>
                  {h.name} · #{h.id}
                </summary>
                <pre>{pretty(h.result)}</pre>
              </details>
            ))}
          </section>
        </>
      )}
    </main>
  );
}
