import React, { useEffect, useRef, useState } from "react";
import { request } from "./lib/api";
import "./field.css";
const api = (p: string, b?: any) =>
  request(
    "/customer-status/v1" + p,
    b
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(b),
        }
      : {},
  );
const statuses = [
  "all_systems_operational",
  "investigating",
  "service_disruption",
  "maintenance_underway",
  "monitoring_recovery",
  "resolved",
];
const pretty = (v: any) => JSON.stringify(v, null, 2);
export default function CustomerCommunications() {
  const [me, M] = useState<any>(),
    [policy, P] = useState<any>(),
    [areas, A] = useState<any[]>([]),
    [cases, C] = useState<any[]>([]),
    [sources, SO] = useState<any>(),
    [detail, D] = useState<any>(),
    [selected, S] = useState(""),
    [impact, I] = useState<any>(),
    [error, E] = useState(""),
    [notice, N] = useState(""),
    [busy, B] = useState(false),
    [reason, R] = useState(""),
    [approved, OK] = useState(false),
    [sourceType, ST] = useState("incident"),
    [sourceId, SI] = useState(""),
    [serviceAffecting, SA] = useState(false),
    [workId, WI] = useState(""),
    [scheduleId, SC] = useState(""),
    [title, T] = useState(""),
    [description, DE] = useState(""),
    [status, PS] = useState("investigating"),
    [areaIds, AI] = useState<string[]>([]),
    [eta, ETA] = useState(""),
    [etaApproved, EA] = useState(false),
    [privacy, PR] = useState(false),
    [proof, PF] = useState(""),
    [withdraw, W] = useState(false),
    [kind, K] = useState("outage"),
    [event, EV] = useState("tower_outage"),
    [confirmed, CF] = useState(false),
    [update, UP] = useState(0),
    [areaName, AN] = useState(""),
    [county, COUNTY] = useState(""),
    [technology, TECH] = useState("unknown"),
    [publicImpact, PUBLICIMPACT] = useState("general"),
    [incidentFilter, IF] = useState(""),
    [siteFilter, SF] = useState(""),
    [areaFilter, AF] = useState(""),
    [since, SIN] = useState(""),
    [until, UNT] = useState(""),
    [offset, OF] = useState(0),
    [more, MORE] = useState<number | null>(null);
  const generation = useRef(0);
  const safe = approved && reason.trim().length >= 10 && !busy;
  const mutation = () => ({ operation_key: crypto.randomUUID(), reason });
  async function load() {
    const query = new URLSearchParams({
      offset: String(offset),
      ...(incidentFilter ? { incident_id: incidentFilter } : {}),
      ...(siteFilter ? { site_id: siteFilter } : {}),
      ...(areaFilter ? { area_id: areaFilter } : {}),
      ...(since ? { since: new Date(since).toISOString() } : {}),
      ...(until ? { until: new Date(until).toISOString() } : {}),
    });
    const [m, p, a, c, src] = await Promise.all([
      api("/me"),
      api("/policy"),
      api("/areas"),
      api("/cases?" + query),
      api("/sources"),
    ]);
    M(m);
    P(p);
    A(a.items);
    C(c.items);
    MORE(c.next_offset);
    SO(src);
  }
  async function open(id: string) {
    const g = ++generation.current;
    S(id);
    D(undefined);
    I(undefined);
    PF("");
    T("");
    DE("");
    AI([]);
    PS("investigating");
    ETA("");
    EA(false);
    PR(false);
    W(false);
    CF(false);
    UP(0);
    if (!id) return;
    const d = await api("/cases/" + id);
    if (g === generation.current) {
      D(d);
      K(d.maintenance_id ? "maintenance" : "outage");
      EV(d.maintenance_id ? "planned_maintenance" : "tower_outage");
    }
  }
  async function refresh() {
    await load();
    if (selected) {
      const g = ++generation.current;
      const d = await api("/cases/" + selected);
      if (g === generation.current) D(d);
    }
  }
  async function act(fn: () => Promise<void>) {
    B(true);
    E("");
    N("");
    try {
      await fn();
    } catch (e) {
      E((e as Error).message);
    } finally {
      B(false);
    }
  }
  useEffect(() => {
    load().catch((e) => E(e.message));
  }, [offset]);
  return (
    <main className="fieldApp">
      <header>
        <a href="/">← NOC</a>
        <h1>Customer network communications</h1>
        <p>
          Powercode owns customer accounts, billing and login. This page manages
          approved network communications only.
        </p>
        <p role="status">
          External SMS/email delivery disabled. Public announcements and SMS
          require independent administrator approval.
        </p>
      </header>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!me ? (
        <p>Loading authorized communications…</p>
      ) : (
        <>
          <section>
            <h2>Review controls</h2>
            <label>
              Communication change reason
              <input
                aria-label="Communication change reason"
                value={reason}
                onChange={(e) => R(e.target.value)}
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={approved}
                onChange={(e) => OK(e.target.checked)}
              />
              I approve documentation changes only; no real delivery.
            </label>
            <p>
              Public gateway:{" "}
              {policy?.enabled ? "enabled by administrator" : "disabled"} ·
              Baseline: {policy?.baseline_status} · Recovery stability:{" "}
              {policy?.stability_seconds}s and at least{" "}
              {policy?.minimum_samples} samples.
            </p>
            <a href="/?notifications=home">
              Existing notification templates, provider simulation and queue
              controls
            </a>{" "}
            <a href="/?down-detector=home">Native outage detection</a>
          </section>
          <section>
            <h2>Communication dashboard</h2>
            <div className="fieldCards">
              <label>
                Incident filter
                <input
                  value={incidentFilter}
                  onChange={(e) => IF(e.target.value)}
                  type="number"
                  min="1"
                />
              </label>
              <label>
                Tower identifier filter
                <input
                  value={siteFilter}
                  onChange={(e) => SF(e.target.value)}
                  type="number"
                  min="1"
                />
              </label>
              <label>
                Public area filter
                <select value={areaFilter} onChange={(e) => AF(e.target.value)}>
                  <option value="">All areas</option>
                  {areas.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Since (local)
                <input
                  type="datetime-local"
                  value={since}
                  onChange={(e) => SIN(e.target.value)}
                />
              </label>
              <label>
                Until (local)
                <input
                  type="datetime-local"
                  value={until}
                  onChange={(e) => UNT(e.target.value)}
                />
              </label>
            </div>
            <button disabled={busy} onClick={() => act(load)}>
              Apply communication filters
            </button>
            <label>
              Communication case
              <select
                aria-label="Communication case"
                disabled={busy}
                value={selected}
                onChange={(e) => act(() => open(e.target.value))}
              >
                <option value="">Select…</option>
                {cases.map((c) => (
                  <option key={c.id} value={c.id}>
                    Case {c.id} ·{" "}
                    {c.incident_id
                      ? "incident " + c.incident_id
                      : "maintenance " + c.maintenance_id}
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={busy || offset === 0}
              onClick={() => OF(Math.max(0, offset - 50))}
            >
              Previous cases
            </button>
            <button disabled={busy || more === null} onClick={() => OF(more!)}>
              Next cases
            </button>
          </section>
          <section>
            <h2>Link an existing source</h2>
            <label>
              Source type
              <select
                value={sourceType}
                onChange={(e) => {
                  ST(e.target.value);
                  SI("");
                }}
              >
                <option value="incident">Existing incident</option>
                <option value="maintenance">
                  Existing maintenance announcement
                </option>
              </select>
            </label>
            <label>
              Source identifier
              <input
                aria-label="Source identifier"
                type="number"
                min="1"
                value={sourceId}
                onChange={(e) => SI(e.target.value)}
              />
            </label>
            <details>
              <summary>Available internal sources (not public)</summary>
              <pre>{pretty(sources)}</pre>
            </details>
            {sourceType === "maintenance" && (
              <>
                <label>
                  <input
                    type="checkbox"
                    checked={serviceAffecting}
                    onChange={(e) => SA(e.target.checked)}
                  />
                  Operator confirms maintenance may interrupt service.
                </label>
                <label>
                  Existing field work order
                  <input
                    type="number"
                    min="1"
                    value={workId}
                    onChange={(e) => WI(e.target.value)}
                  />
                </label>
                <label>
                  Existing maintenance schedule
                  <input
                    type="number"
                    min="1"
                    value={scheduleId}
                    onChange={(e) => SC(e.target.value)}
                  />
                </label>
              </>
            )}
            <button
              disabled={!safe || !sourceId}
              onClick={() =>
                act(async () => {
                  const c = await api("/cases", {
                    ...mutation(),
                    incident_id:
                      sourceType === "incident" ? Number(sourceId) : null,
                    maintenance_id:
                      sourceType === "maintenance" ? Number(sourceId) : null,
                    service_affecting: serviceAffecting,
                    work_order_id: workId ? Number(workId) : null,
                    schedule_id: scheduleId ? Number(scheduleId) : null,
                  });
                  await load();
                  await open(String(c.id));
                  N("Existing source linked. Nothing published or queued.");
                })
              }
            >
              Link source without publishing
            </button>
          </section>
          {detail && (
            <section aria-label="Communication detail">
              <h2>Case {detail.id}</h2>
              <button disabled={busy} onClick={() => act(refresh)}>
                Refresh communication case
              </button>
              <p>
                Native source status: {detail.source.status} · Case version{" "}
                {detail.version} · Published revision:{" "}
                {detail.published_revision_id ?? "None"}
              </p>
              {detail.incident_id && (
                <a href={"/?incident=" + detail.incident_id}>
                  Review native incident and closure
                </a>
              )}
              <p>
                Work order {detail.work_order_id ?? "not linked"} · Maintenance
                schedule {detail.schedule_id ?? "not linked"}
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    I(await api("/cases/" + detail.id + "/impact"));
                  })
                }
              >
                Preview affected customers
              </button>
              {impact && (
                <div aria-label="Recipient preview">
                  <p>
                    Eligible SMS contacts: {impact.eligible_count}. Impact is
                    documented dependency evidence, not an end-user connectivity
                    test.
                  </p>
                  <pre>{pretty(impact.classification_counts)}</pre>
                  <details>
                    <summary>Mapping evidence and excluded recipients</summary>
                    <pre>{pretty(impact)}</pre>
                  </details>
                </div>
              )}
              <h3>Restoration evidence</h3>
              <p>
                One successful poll does not verify restoration. Assess recorded
                monitoring stability before preparing a resolved announcement or
                restoration SMS.
              </p>
              <button
                disabled={!safe}
                onClick={() =>
                  act(async () => {
                    const r = await api(
                      "/cases/" + detail.id + "/recovery",
                      mutation(),
                    );
                    if (r.verified) PF(String(r.id));
                    N(
                      r.verified
                        ? "Recorded monitoring recovery verified; separate approval still required."
                        : "Recovery is unverified; inspect missing or unstable evidence.",
                    );
                    await refresh();
                  })
                }
              >
                Assess stored recovery evidence
              </button>
              <label>
                Verified recovery assessment identifier
                <input
                  aria-label="Recovery assessment identifier"
                  type="number"
                  min="1"
                  value={proof}
                  onChange={(e) => PF(e.target.value)}
                />
              </label>
              <details>
                <summary>Recorded recovery assessments</summary>
                <pre>{pretty(detail.recoveries)}</pre>
              </details>
              <h3>Public announcement draft</h3>
              <p>
                Write generic public copy. Internal incident text and tower
                names are never copied automatically.
              </p>
              <label>
                Public title
                <input
                  aria-label="Public title"
                  value={title}
                  onChange={(e) => T(e.target.value)}
                />
              </label>
              <label>
                Public description
                <textarea
                  aria-label="Public description"
                  rows={4}
                  value={description}
                  onChange={(e) => DE(e.target.value)}
                />
              </label>
              <label>
                Public status
                <select
                  aria-label="Public status"
                  value={status}
                  onChange={(e) => PS(e.target.value)}
                >
                  {statuses.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label>
                Interruption classification
                <select aria-label="Interruption classification" value={publicImpact} onChange={e => PUBLICIMPACT(e.target.value)}>
                  {["general", "partial", "major", "degraded"].map(v => <option key={v}>{v}</option>)}
                </select>
              </label>
              <label>
                Approved public areas
                <select
                  aria-label="Approved public areas"
                  multiple
                  value={areaIds}
                  onChange={(e) =>
                    AI([...e.target.selectedOptions].map((o) => o.value))
                  }
                >
                  {areas.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Optional approved restoration estimate (local)
                <input
                  type="datetime-local"
                  value={eta}
                  onChange={(e) => ETA(e.target.value)}
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={etaApproved}
                  onChange={(e) => EA(e.target.checked)}
                />
                Administrator explicitly approves the estimate.
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={privacy}
                  onChange={(e) => PR(e.target.checked)}
                />
                Public text and area names contain no private network or
                customer information.
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={withdraw}
                  onChange={(e) => W(e.target.checked)}
                />
                Prepare withdrawal of the existing publication.
              </label>
              <button
                disabled={
                  !safe ||
                  !privacy ||
                  !areaIds.length ||
                  title.length < 5 ||
                  description.length < 10
                }
                onClick={() =>
                  act(async () => {
                    await api("/cases/" + detail.id + "/revisions", {
                      ...mutation(),
                      expected_version: detail.version,
                      title,
                      description,
                      status,
                      impact: status === "service_disruption" ? publicImpact : "general",
                      area_ids: areaIds.map(Number),
                      privacy_reviewed: privacy,
                      withdraw,
                      recovery_id: proof ? Number(proof) : null,
                      estimated_restoration_at: eta
                        ? new Date(eta).toISOString()
                        : null,
                      restoration_estimate_approved: etaApproved,
                    });
                    await refresh();
                    N("Public draft saved privately. It is not published.");
                  })
                }
              >
                Save private public-announcement draft
              </button>
              <div aria-label="Public approval revisions">
                {detail.revisions.map((r: any) => (
                  <article key={r.id}>
                    <h4>
                      Public revision {r.number} · {r.state}
                    </h4>
                    <p>{r.payload.title}</p>
                    <p>{r.payload.description}</p>
                    <p>
                      {r.payload.areas.join(", ")} · {r.payload.status}
                    </p>
                    {r.payload.estimated_restoration_at && (
                      <p>
                        Explicitly approved estimate:{" "}
                        {r.payload.estimated_restoration_at}
                      </p>
                    )}
                    <button
                      disabled={!safe || r.state !== "draft"}
                      onClick={() =>
                        act(async () => {
                          await api("/revisions/" + r.id + "/transition", {
                            ...mutation(),
                            expected_version: detail.version,
                            action: "request_approval",
                          });
                          await refresh();
                        })
                      }
                    >
                      Request public approval #{r.id}
                    </button>
                    {me.role === "admin" && (
                      <>
                        <button
                          disabled={!safe || r.state !== "pending_approval"}
                          onClick={() =>
                            act(async () => {
                              await api("/revisions/" + r.id + "/transition", {
                                ...mutation(),
                                expected_version: detail.version,
                                action: "approve",
                              });
                              await refresh();
                            })
                          }
                        >
                          Approve public revision #{r.id}
                        </button>
                        <button
                          disabled={!safe || r.state !== "pending_approval"}
                          onClick={() =>
                            act(async () => {
                              await api("/revisions/" + r.id + "/transition", {
                                ...mutation(),
                                expected_version: detail.version,
                                action: "reject",
                              });
                              await refresh();
                            })
                          }
                        >
                          Reject public revision #{r.id}
                        </button>
                        <button
                          disabled={!safe || r.state !== "approved"}
                          onClick={() =>
                            act(async () => {
                              await api("/revisions/" + r.id + "/transition", {
                                ...mutation(),
                                expected_version: detail.version,
                                action: "publish",
                              });
                              await refresh();
                              N(
                                "Approved public revision applied. SMS approval remains separate.",
                              );
                            })
                          }
                        >
                          {r.withdraw
                            ? "Apply approved withdrawal"
                            : "Publish approved public revision"}{" "}
                          #{r.id}
                        </button>
                      </>
                    )}
                  </article>
                ))}
              </div>
              <h3>Related internal Slack draft</h3>
              <button disabled={!safe} onClick={() => act(async () => {
                const result = await api("/cases/" + detail.id + "/slack-draft", mutation());
                N("Slack draft " + result.plan_id + " prepared. Provider release and source approval remain separate; nothing sent.");
              })}>Prepare related Slack draft only</button>
              <h3>SMS recipient and message draft</h3>
              <label>
                Notification transition
                <select
                  aria-label="Notification transition"
                  value={kind}
                  onChange={(e) => K(e.target.value)}
                >
                  {[
                    "outage",
                    "update",
                    "extended",
                    "maintenance",
                    "rescheduled",
                    "cancelled",
                    "restoration",
                  ].map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </label>
              <label>
                Native event evidence
                <select value={event} onChange={(e) => EV(e.target.value)}>
                  {[
                    "tower_outage",
                    "backhaul_outage",
                    "sector_outage",
                    "fiber_outage",
                    "tower_power",
                    "equipment_failure",
                    "severe_degradation",
                    "planned_maintenance",
                    "restoration",
                  ].map((e) => (
                    <option key={e}>{e}</option>
                  ))}
                </select>
              </label>
              <label>
                Approved update number
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={update}
                  onChange={(e) => UP(Number(e.target.value))}
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => CF(e.target.checked)}
                />
                Operator confirms customer-impacting incident evidence.
              </label>
              <button
                disabled={!safe}
                onClick={() =>
                  act(async () => {
                    await api("/cases/" + detail.id + "/messages", {
                      ...mutation(),
                      kind,
                      event_type: event,
                      impact_confirmed: confirmed,
                      update_number: update,
                      recovery_id: proof ? Number(proof) : null,
                    });
                    await refresh();
                    N(
                      "SMS draft and recipient preview prepared; nothing sent.",
                    );
                  })
                }
              >
                Prepare SMS draft only
              </button>
              {detail.messages.map((m: any) => (
                <article key={m.id}>
                  <h4>
                    SMS draft {m.id} · {m.kind} · {m.status}
                  </h4>
                  <p>{m.message}</p>
                  <p>
                    Approved by: {m.approved_by ?? "Nobody"} · Due: {m.due_at}
                  </p>
                  <details>
                    <summary>
                      Recipients, exclusions and delivery outcomes
                    </summary>
                    <pre>
                      {pretty({
                        recipients: m.recipients,
                        impacts: m.impacts,
                        counts: m.delivery_counts,
                        deliveries: m.deliveries,
                      })}
                    </pre>
                  </details>
                  {me.role === "admin" &&
                    ["approve", "reject", "cancel"].map((action) => (
                      <button
                        key={action}
                        disabled={
                          !safe ||
                          (action === "approve"
                            ? m.status !== "draft"
                            : !["draft", "approved"].includes(m.status))
                        }
                        onClick={() =>
                          act(async () => {
                            await api("/messages/" + m.id + "/decision", {
                              ...mutation(),
                              action,
                              fingerprint: m.fingerprint,
                            });
                            await refresh();
                            N(
                              "SMS decision recorded independently of public publication. External delivery remains disabled.",
                            );
                          })
                        }
                      >
                        {action === "approve"
                          ? "Approve mock SMS queue"
                          : action === "reject"
                            ? "Reject SMS draft"
                            : "Cancel pending SMS"}{" "}
                        #{m.id}
                      </button>
                    ))}
                </article>
              ))}
              <details>
                <summary>Audited communication history</summary>
                <pre>{pretty(detail.history)}</pre>
              </details>
            </section>
          )}
          {me.role === "admin" && policy && (
            <section>
              <h2>Administrator public gateway setup</h2>
              <label>
                <input
                  type="checkbox"
                  checked={policy.enabled}
                  onChange={(e) => P({ ...policy, enabled: e.target.checked })}
                />
                Enable approved public status API
              </label>
              <label>
                Explicitly reviewed global baseline
                <select
                  aria-label="Explicitly reviewed global baseline"
                  value={policy.baseline_status}
                  onChange={(e) =>
                    P({ ...policy, baseline_status: e.target.value })
                  }
                >
                  {[
                    "all_systems_operational",
                    "investigating",
                    "service_disruption",
                  ].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label>
                Recovery stability seconds
                <input
                  type="number"
                  min="60"
                  max="3600"
                  value={policy.stability_seconds}
                  onChange={(e) =>
                    P({ ...policy, stability_seconds: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Required consecutive samples
                <input
                  type="number"
                  min="3"
                  max="20"
                  value={policy.minimum_samples}
                  onChange={(e) =>
                    P({ ...policy, minimum_samples: Number(e.target.value) })
                  }
                />
              </label>
              <button
                disabled={!safe}
                onClick={() =>
                  act(async () => {
                    await api("/policy", {
                      ...mutation(),
                      expected_revision: policy.revision,
                      enabled: policy.enabled,
                      baseline_status: policy.baseline_status,
                      public_baseline_reviewed: approved,
                      stability_seconds: policy.stability_seconds,
                      minimum_samples: policy.minimum_samples,
                      maximum_gap_seconds: policy.maximum_gap_seconds,
                    });
                    await refresh();
                    N("Public baseline and policy explicitly reviewed.");
                  })
                }
              >
                Save approved public baseline and policy
              </button>
              <label>
                Generic public service-area name
                <input
                  aria-label="Generic public service-area name"
                  value={areaName}
                  onChange={(e) => AN(e.target.value)}
                />
              </label>
              <label>Public county grouping <input aria-label="Public county grouping" value={county} onChange={e => COUNTY(e.target.value)} /></label>
              <label>Public service technology <select aria-label="Public service technology" value={technology} onChange={e => TECH(e.target.value)}>{["unknown", "wireless", "fiber", "hybrid", "regional"].map(v => <option key={v}>{v}</option>)}</select></label>
              <button
                disabled={!safe || areaName.length < 3}
                onClick={() =>
                  act(async () => {
                    await api("/areas", {
                      ...mutation(),
                      label: areaName,
                      county: county || null,
                      technology,
                      publicly_approved: true,
                    });
                    await load();
                    AN("");
                  })
                }
              >
                Approve public area name
              </button>
              <button
                disabled={!safe}
                onClick={() =>
                  act(async () => {
                    const r = await api("/templates/initialize", mutation());
                    N(
                      "Native templates initialized without overwriting existing operator edits: " +
                        r.created.join(", "),
                    );
                  })
                }
              >
                Initialize editable operational SMS templates
              </button>
            </section>
          )}
        </>
      )}
      <style>{`pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:24rem;overflow:auto}.fieldApp select[multiple]{min-height:8rem}`}</style>
    </main>
  );
}
