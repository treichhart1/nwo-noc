/** Public-only WordPress widget. No NOC auth, customer lookup or private API imports. */
import "./public-status.css";
const labels: Record<string, string> = {
  all_systems_operational: "All systems operational",
  investigating: "Investigating",
  service_disruption: "Service disruption",
  maintenance_underway: "Maintenance underway",
  monitoring_recovery: "Monitoring recovery",
  resolved: "Resolved",
};
function node(tag: string, text?: string) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  return n;
}
function date(value: unknown) {
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value)))
    throw new Error("Invalid public timestamp");
  return new Date(value).toLocaleString();
}
function validate(data: any) {
  const allowed = [
    "api_version",
    "overall_status",
    "last_public_update",
    "basis",
    "incidents",
    "maintenance",
    "resolved",
    "next_history_offset",
  ];
  if (
    !data ||
    Object.keys(data).some((k) => !allowed.includes(k)) ||
    data.api_version !== "1" ||
    !labels[data.overall_status]
  )
    throw new Error("Invalid public contract");
  date(data.last_public_update);
  for (const key of ["incidents", "maintenance", "resolved"]) {
    if (!Array.isArray(data[key]) || data[key].length > 200)
      throw new Error("Invalid public list");
    for (const item of data[key]) {
      if (
        Object.keys(item).some(
          (k) =>
            ![
              "id",
              "title",
              "description",
              "status",
              "areas",
              "starts_at",
              "ends_at",
              "estimated_restoration_at",
              "updated_at",
            ].includes(k),
        ) ||
        !labels[item.status] ||
        typeof item.title !== "string" ||
        item.title.length > 120 ||
        typeof item.description !== "string" ||
        item.description.length > 1200 ||
        !Array.isArray(item.areas) ||
        item.areas.length > 20 ||
        item.areas.some((a: any) => typeof a !== "string" || a.length > 100)
      )
        throw new Error("Invalid approved announcement");
      date(item.updated_at);
      for (const k of ["starts_at", "ends_at", "estimated_restoration_at"])
        if (item[k]) date(item[k]);
    }
  }
  return data;
}
function render(root: HTMLElement, data: any) {
  root.replaceChildren(node("h1", "Network status"));
  const overview = node("section");
  overview.className =
    "status-summary " +
    (data.overall_status === "all_systems_operational"
      ? "status-operational"
      : "status-investigating");
  overview.setAttribute("aria-label", "Overall public network status");
  overview.setAttribute("aria-live", "polite");
  overview.append(
    node("h2", labels[data.overall_status]),
    node("p", "Last public update: " + date(data.last_public_update)),
  );
  root.append(overview);
  const note = node(
    "p",
    "Status reflects administrator-approved announcements and may lag an incident. It does not verify an individual customer connection.",
  );
  note.className = "status-note";
  root.append(note);
  if (Date.now() - Date.parse(data.last_public_update) > 86400000)
    root.append(
      node(
        "p",
        "No recent approved update. Please contact support through the usual channels if you need assistance.",
      ),
    );
  for (const [key, title] of [
    ["incidents", "Active service updates"],
    ["maintenance", "Planned maintenance"],
    ["resolved", "Approved restoration history"],
  ]) {
    const section = node("section");
    section.append(node("h2", title));
    if (!data[key].length)
      section.append(node("p", "No approved announcements in this category."));
    for (const item of data[key]) {
      const article = node("article");
      article.className =
        item.status === "service_disruption" ? "status-disruption" : "";
      article.append(
        node("h3", item.title),
        node("p", labels[item.status]),
        node("p", item.description),
        node("p", "Service areas: " + item.areas.join(", ")),
      );
      if (item.starts_at)
        article.append(node("p", "Scheduled start: " + date(item.starts_at)));
      if (item.ends_at)
        article.append(node("p", "Scheduled end: " + date(item.ends_at)));
      if (item.estimated_restoration_at)
        article.append(
          node(
            "p",
            "Administrator-approved restoration estimate: " +
              date(item.estimated_restoration_at),
          ),
        );
      article.append(node("p", "Updated: " + date(item.updated_at)));
      section.append(article);
    }
    root.append(section);
  }
  const refresh = node(
    "button",
    "Refresh approved status",
  ) as HTMLButtonElement;
  refresh.type = "button";
  refresh.onclick = () => root.dispatchEvent(new Event("nwo-status-refresh"));
  root.append(refresh);
}
async function boundedJSON(r: Response) {
  const reader = r.body?.getReader();
  if (!reader) throw new Error("Unavailable public response");
  let size = 0;
  const parts: Uint8Array[] = [];
  while (true) {
    const p = await reader.read();
    if (p.done) break;
    size += p.value.byteLength;
    if (size > 524288) {
      await reader.cancel();
      throw new Error("Public response too large");
    }
    parts.push(p.value);
  }
  const data = new Uint8Array(size);
  let offset = 0;
  for (const p of parts) {
    data.set(p, offset);
    offset += p.byteLength;
  }
  return JSON.parse(new TextDecoder().decode(data));
}
for (const element of document.querySelectorAll<HTMLElement>(
  "[data-nwo-network-status]",
)) {
  let busy = false;
  async function refresh() {
    if (busy) return;
    busy = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      if (
        location.protocol !== "https:" &&
        !["localhost", "127.0.0.1"].includes(location.hostname)
      )
        throw new Error("HTTPS required");
      const url = new URL(
        element.dataset.statusEndpoint || "/public/network-status/v1",
        location.href,
      );
      if (
        url.origin !== location.origin ||
        !["/public/network-status/v1", "/network-status-api/v1"].includes(
          url.pathname,
        ) ||
        url.search ||
        url.hash ||
        url.username ||
        url.password
      )
        throw new Error("Approved same-origin public endpoint required");
      const r = await fetch(url, {
        credentials: "omit",
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      if (!r.ok) throw new Error("Public status unavailable");
      render(element, validate(await boundedJSON(r)));
    } catch {
      element.replaceChildren(
        node("h1", "Network status"),
        node(
          "p",
          "Network status temporarily unavailable. Please contact support through the usual channels.",
        ),
      );
    } finally {
      clearTimeout(timer);
      busy = false;
    }
  }
  element.addEventListener("nwo-status-refresh", refresh);
  void refresh();
  setInterval(() => {
    if (!document.hidden) void refresh();
  }, 60000);
}
