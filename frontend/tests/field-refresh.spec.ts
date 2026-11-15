import { test, expect } from "@playwright/test";
test.skip(
  process.env.NOC101_FIELD_BROWSER !== "true",
  "Requires the isolated NOC browser harness",
);
test("late work-order refresh cannot erase a newer recorded inspection", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem("nwo_token", "synthetic-field-browser-token"),
  );
  const before = {
    id: 41,
    number: "SYN-WO-41",
    description: "Synthetic field read ordering",
    priority: "normal",
    status: "in_progress",
    revision: 2,
    site_id: 1,
    technician_id: 9876,
    required_equipment: [],
    inspections: [],
    attachments: [],
    events: [],
    next_offset: null,
    template: null,
  };
  const after = {
    ...before,
    revision: 3,
    inspections: [
      {
        id: 77,
        inspected_at: "2026-01-01T10:00:00Z",
        technician_id: 9876,
        qualified_review: false,
        notes: "Synthetic already-recorded inspection",
        snapshot: {},
        results: [],
      },
    ],
  };
  let count = 0;
  let seen!: () => void;
  const waiting = new Promise<void>((resolve) => (seen = resolve));
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  let delivered!: () => void;
  const finished = new Promise<void>((resolve) => (delivered = resolve));
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    const send = (value: any, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(value),
      });
    if (p === "/api/applications/work-context/41") return send({detail:"No customer application"},404);
    if (p === "/api/field/me")
      return send({
        id: 9876,
        username: "synthetic-field-reader",
        role: "operator",
      });
    if (p === "/api/field/work-orders")
      return send({ items: [before], next_offset: null });
    if (p === "/api/field/templates")
      return send({ items: [], default_items: [] });
    if (p === "/api/field/work-orders/41") {
      count++;
      if (count === 1) {
        seen();
        await gate;
        await send(before);
        delivered();
        return;
      }
      return send(after);
    }
    if (p === "/api/field/towers/1")
      return send(
        { detail: "Optional synthetic tower context unavailable" },
        503,
      );
    return send({ items: [], next_offset: null });
  });
  try {
    await page.goto("/?field=home&work=41");
    await waiting;
    await page
      .getByRole("button", { name: "Open work order #41", exact: true })
      .click();
    const work = page.getByRole("region", {
      name: "Field work order",
      exact: true,
    });
    await expect(work).toContainText("Inspection#77");
    const oldResponse = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/field/work-orders/41",
    );
    release();
    await finished;
    await (await oldResponse).finished();
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(work).toContainText("Inspection#77");
    await expect(work).toContainText("revision3");
  } finally {
    release();
  }
});
