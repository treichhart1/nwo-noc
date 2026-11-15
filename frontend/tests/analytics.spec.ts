import { test, expect } from "@playwright/test";
import fs from "node:fs";
test.skip(
  process.env.NOC108_ANALYTICS_BROWSER !== "true",
  "Requires offline synthetic analytics fixture",
);
function fixture() {
  const p = process.env.NOC_ACCEPTANCE_STATE!;
  const env = Object.fromEntries(
    fs
      .readFileSync(p + "/acceptance.env", "utf8")
      .trim()
      .split("\n")
      .map((l) => {
        const i = l.indexOf("=");
        return [l.slice(0, i), l.slice(i + 1)];
      }),
  );
  return {
    env,
    f: JSON.parse(fs.readFileSync(p + "/analytics-fixture.json", "utf8")),
  };
}
async function login(page: any) {
  const { env, f } = fixture();
  await page.goto("/?monitoring-analytics=home");
  await page.locator(".loginCard input").nth(0).fill(f.username);
  await page.locator("input[type=password]").fill(env.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Network Analytics", exact: true }),
  ).toBeVisible();
  return f;
}
test("mobile private reports distinguish missing observations and export recorded data", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const outside: string[] = [];
  await page.route("**/*", async (route) => {
    if (new URL(route.request().url()).origin !== "http://127.0.0.1:18090") {
      outside.push(route.request().url());
      await route.abort();
    } else await route.continue();
  });
  const f = await login(page);
  await page.getByLabel("Metric", { exact: true }).selectOption("cpu_percent");
  await page
    .getByLabel("Device identifier", { exact: true })
    .fill(String(f.device_id));
  await page
    .getByRole("button", { name: "Load recorded report", exact: true })
    .click();
  await expect(
    page.getByRole("img", { name: "Recorded measurement trend" }),
  ).toBeVisible();
  await expect(page.getByTestId("analytics-range")).toContainText(
    "native_recorded_views",
  );
  const download = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Export authorized CSV", exact: true })
    .click();
  expect((await download).suggestedFilename()).toBe(
    "noc-recorded-analytics.csv",
  );
  await page.getByLabel("Metric", { exact: true }).selectOption("rssi_dbm");
  await page
    .getByRole("button", { name: "Load recorded report", exact: true })
    .click();
  await expect(
    page.getByText("No valid measurements in this scope and time range.", {
      exact: false,
    }),
  ).toBeVisible();
  expect(outside).toEqual([]);
});
test("synthetic health is explicitly unverified and provisioning never writes remotely", async ({
  page,
}) => {
  await login(page);
  await page
    .getByRole("button", {
      name: "Check synthetic Grafana fixture",
      exact: true,
    })
    .click();
  await expect(page.getByTestId("analytics-health")).toContainText(
    "Live connectivity: unverified",
  );
  await expect(page.getByTestId("analytics-output")).toContainText(
    "synthetic_mock",
  );
  const download = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Export provisioning templates", exact: true })
    .click();
  expect((await download).suggestedFilename()).toBe(
    "noc-grafana-provisioning.json",
  );
  await expect(page.getByTestId("analytics-output")).toContainText(
    "No remote writes",
  );
  await expect(
    page.getByRole("button", {
      name: "Check approved private Grafana",
      exact: true,
    }),
  ).toBeDisabled();
});
test("approved dashboard navigation carries no NOC token and rejects arbitrary identifiers", async ({
  page,
}) => {
  await login(page);
  await page
    .getByRole("button", { name: "Prepare secure dashboard link", exact: true })
    .click();
  const link = page.getByRole("link", {
    name: "Open approved private Grafana dashboard",
  });
  await expect(link).toHaveAttribute(
    "href",
    /^https:\/\/grafana.synthetic.invalid\/d\/noc-network\/noc\?/,
  );
  expect(await link.getAttribute("href")).not.toContain("token");
  const token = await page.evaluate(() => localStorage.getItem("nwo_token"));
  const r = await page.request.post("/api/analytics/v1/dashboard-link", {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      operation_key: crypto.randomUUID(),
      reason: "Synthetic browser verifies closed dashboard allowlist",
      dashboard_uid: "../admin",
      query: { preset: "24h", metric: "cpu_percent" },
      embed: false,
    },
  });
  expect(r.status()).toBeGreaterThanOrEqual(400);
});
