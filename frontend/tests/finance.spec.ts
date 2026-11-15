import { test, expect } from "@playwright/test";
import fs from "node:fs";
test.skip(
  process.env.NOC107_FINANCE_BROWSER !== "true",
  "Requires isolated synthetic financial acceptance fixture",
);
function fixture() {
  const dir = process.env.NOC_ACCEPTANCE_STATE!;
  const env = Object.fromEntries(
    fs
      .readFileSync(dir + "/acceptance.env", "utf8")
      .trim()
      .split("\n")
      .map((l) => {
        const i = l.indexOf("=");
        return [l.slice(0, i), l.slice(i + 1)];
      }),
  );
  return {
    env,
    f: JSON.parse(fs.readFileSync(dir + "/finance-fixture.json", "utf8")),
  };
}
async function login(page: any) {
  page.setDefaultTimeout(15000);
  const { env, f } = fixture();
  await page.goto("/?network-roi=home");
  await page.locator(".loginCard input").nth(0).fill(f.username);
  await page.locator("input[type=password]").fill(env.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Network profitability", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Financial scope", exact: true })
    .selectOption(String(f.tower_scope_id));
  return f;
}
async function workflow(page: any, kind: string, body: any) {
  await page
    .getByRole("combobox", { name: "Workflow", exact: true })
    .selectOption(kind);
  await page
    .getByLabel("Financial workflow JSON", { exact: true })
    .fill(JSON.stringify(body));
  await page
    .getByRole("button", { name: "Submit reviewed workflow", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "Saved internal financial record",
  );
}
test("mobile financial dashboard labels incomplete inputs and separates revenue bases", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const external: string[] = [];
  await page.route("**/*", async (route) => {
    if (new URL(route.request().url()).origin !== "http://127.0.0.1:18090") {
      external.push(route.request().url());
      await route.abort();
    } else await route.continue();
  });
  await login(page);
  const report = page.getByRole("region", {
    name: "Financial report",
    exact: true,
  });
  await expect(report).toContainText("Incomplete inputs");
  await expect(report).toContainText("$1,000.00");
  await page
    .getByRole("combobox", { name: "Revenue basis", exact: true })
    .selectOption("collected");
  await expect(report).toContainText("verified collected receipts");
  await expect(report.locator("dd").first()).toHaveText("Unavailable");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(external).toEqual([]);
});
test("explicit completeness enables contribution and scenarios preserve actual facts", async ({
  page,
}) => {
  const f = await login(page);
  await workflow(page, "coverage", {
    scope_id: f.tower_scope_id,
    revenue_basis: "contracted",
    expense_basis: "actual",
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
      "Synthetic administrator attested complete finance categories including documented zero expenses",
  });
  const report = page.getByRole("region", {
    name: "Financial report",
    exact: true,
  });
  await expect(report).toContainText("Completeness explicitly attested");
  await expect(report).toContainText("$700.00");
  await workflow(page, "scenario", {
    scope_id: f.tower_scope_id,
    name: "Browser synthetic backhaul upgrade",
    revenue_basis: "contracted",
    expense_basis: "actual",
    monthly_revenue_delta: "100",
    monthly_opex_delta: "20",
    capex: "800",
    additional_customers: 2,
    evidence:
      "Synthetic advisory assumptions with no automatic equipment purchase",
  });
  await expect(
    page.getByRole("region", { name: "ROI scenarios", exact: true }),
  ).toContainText("Browser synthetic backhaul upgrade");
  await expect(report).toContainText("$700.00");
});
test("mock revenue requires reviewed preview approval and preserves cash distinction", async ({
  page,
}) => {
  const f = await login(page);
  await workflow(page, "powercode", {
    provider: "powercode_mock",
    charges: [
      {
        external_key: "browser-contract-2",
        service_external_id: f.service_external_id,
        amount: "50.00",
        basis: "invoiced",
        currency: "USD",
        evidence: "Synthetic billed charge, never treated as collected cash",
      },
    ],
  });
  const preview = page.getByRole("region", {
    name: "Financial import preview",
    exact: true,
  });
  await expect(preview).toContainText("dry_run");
  await expect(
    page.getByRole("button", {
      name: "Approve exact financial import",
      exact: true,
    }),
  ).toBeDisabled();
  await page
    .getByLabel("I reviewed source facts, mappings and financial basis", {
      exact: true,
    })
    .check();
  await page
    .getByRole("button", {
      name: "Approve exact financial import",
      exact: true,
    })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "Saved internal financial record",
  );
  await page
    .getByRole("combobox", { name: "Revenue basis", exact: true })
    .selectOption("invoiced");
  await expect(
    page.getByRole("region", { name: "Financial report", exact: true }),
  ).toContainText("$50.00");
  await page
    .getByRole("combobox", { name: "Revenue basis", exact: true })
    .selectOption("collected");
  await expect(
    page.getByRole("region", { name: "Financial report", exact: true }),
  ).toContainText("verified collected receipts");
  await expect(
    page
      .getByRole("region", { name: "Financial report", exact: true })
      .locator("dd")
      .first(),
  ).toHaveText("Unavailable");
});
