import { test, expect } from "@playwright/test";
import fs from "node:fs";
test.skip(
  process.env.NOC109_STATUS_BROWSER !== "true",
  "Requires isolated approved communications fixture",
);
const publicURL = "http://127.0.0.1:18092";
function fixture() {
  const path = process.env.NOC_ACCEPTANCE_STATE!;
  const env = Object.fromEntries(
    fs
      .readFileSync(path + "/acceptance.env", "utf8")
      .trim()
      .split("\n")
      .map((l) => {
        const i = l.indexOf("=");
        return [l.slice(0, i), l.slice(i + 1)];
      }),
  );
  return {
    env,
    f: JSON.parse(
      fs.readFileSync(path + "/customer-status-fixture.json", "utf8"),
    ),
  };
}
async function login(page: any) {
  page.setDefaultTimeout(15000);
  const { env, f } = fixture();
  await page.goto("/?customer-status=home");
  await page.locator(".loginCard input").nth(0).fill(f.username);
  await page.locator("input[type=password]").fill(env.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "Customer network communications",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByLabel("Communication change reason", { exact: true })
    .fill(
      "Synthetic administrator reviews generic public copy and mock-only customer communications",
    );
  await page
    .getByLabel("I approve documentation changes only; no real delivery.", {
      exact: true,
    })
    .check();
  return f;
}
async function publicData(request: any) {
  const r = await request.get(publicURL + "/public/network-status/v1");
  expect(r.ok()).toBe(true);
  return r.json();
}
test("public status is disabled by default and cannot expose private routes", async ({
  page,
}) => {
  await page.goto(publicURL + "/network-status.html");
  await expect(
    page.getByText("Network status temporarily unavailable.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
  for (const path of [
    "/api/devices",
    "/api/auth/me",
    "/api/customer-status/v1/cases",
  ])
    expect((await page.request.get(publicURL + path)).status()).toBe(404);
});
test("mobile administrator independently reviews approves and publishes generic public copy", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const f = await login(page);
  await page
    .getByLabel("Enable approved public status API", { exact: true })
    .check();
  await page
    .getByLabel("Explicitly reviewed global baseline", { exact: true })
    .selectOption("all_systems_operational");
  await page
    .getByRole("button", {
      name: "Save approved public baseline and policy",
      exact: true,
    })
    .click();
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Public baseline and policy explicitly reviewed" }),
  ).toBeVisible();
  await page
    .getByLabel("Communication case", { exact: true })
    .selectOption(String(f.case_id));
  await expect(
    page.getByRole("region", { name: "Communication detail" }),
  ).toContainText("Native source status");
  await page
    .getByLabel("Public title", { exact: true })
    .fill("Service interruption in northwest communities");
  await page
    .getByLabel("Public description", { exact: true })
    .fill(
      "Technicians are investigating a service interruption. Further approved updates will follow.",
    );
  await page
    .getByLabel("Public status", { exact: true })
    .selectOption("service_disruption");
  await page
    .getByLabel("Approved public areas", { exact: true })
    .selectOption([String(f.area_id)]);
  await page
    .getByLabel(
      "Public text and area names contain no private network or customer information.",
      { exact: true },
    )
    .check();
  await page
    .getByRole("button", {
      name: "Save private public-announcement draft",
      exact: true,
    })
    .click();
  const revisions = page.getByLabel("Public approval revisions", {
    exact: true,
  });
  await expect(revisions).toContainText("draft");
  expect((await publicData(page.request)).incidents).toEqual([]);
  await revisions
    .getByRole("button", { name: /^Request public approval/ })
    .click();
  await expect(revisions).toContainText("pending_approval");
  await revisions
    .getByRole("button", { name: /^Approve public revision/ })
    .click();
  await expect(revisions).toContainText("approved");
  expect((await publicData(page.request)).incidents).toEqual([]);
  await revisions
    .getByRole("button", { name: /^Publish approved public revision/ })
    .click();
  await expect(revisions).toContainText("published");
  const data = await publicData(page.request);
  expect(data.incidents).toHaveLength(1);
  const text = JSON.stringify(data);
  for (const secret of [f.private_title, f.customer_phone, f.customer_name])
    if (secret) expect(text).not.toContain(secret);
  expect(text).not.toMatch(/incident_id|site_id|device_id|customer_id/);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("public WordPress component renders only approved information with no authentication or private requests", async ({
  page,
}) => {
  const requests: string[] = [];
  await page.route("**/*", async (route) => {
    const u = new URL(route.request().url());
    requests.push(u.href);
    if (u.origin !== publicURL) await route.abort();
    else await route.continue();
  });
  await page.goto(publicURL + "/network-status.html");
  await expect(
    page.getByRole("heading", { name: "Service disruption", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "Service interruption in northwest communities",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Northwest service communities", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
  expect(requests.every((u) => !new URL(u).pathname.startsWith("/api/"))).toBe(
    true,
  );
  expect(
    await page.evaluate(() => localStorage.getItem("nwo_token")),
  ).toBeNull();
});
test("SMS preview remains a separate unapproved mock draft and recovery is not inferred from incident status", async ({
  page,
}) => {
  const f = await login(page);
  await page
    .getByLabel("Communication case", { exact: true })
    .selectOption(String(f.case_id));
  await page
    .getByRole("button", { name: "Preview affected customers", exact: true })
    .click();
  await expect(
    page.getByLabel("Recipient preview", { exact: true }),
  ).toContainText("Eligible SMS contacts");
  await page
    .getByLabel("Operator confirms customer-impacting incident evidence.", {
      exact: true,
    })
    .check();
  await page
    .getByRole("button", { name: "Prepare SMS draft only", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: /SMS draft .*outage.*draft/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Approve mock SMS queue/ }),
  ).toBeEnabled();
  await page
    .getByRole("button", {
      name: "Assess stored recovery evidence",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Recovery is unverified" }),
  ).toBeVisible();
  const data = await publicData(page.request);
  expect(data.incidents[0].status).toBe("service_disruption");
});
test("public widget refuses a manipulated endpoint before making any private API request", async ({
  page,
}) => {
  const privateRequests: string[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/"))
      privateRequests.push(r.url());
  });
  await page.route("**/network-status.html", async (route) => {
    const r = await route.fetch();
    await route.fulfill({
      response: r,
      body: (await r.text()).replace(
        "/public/network-status/v1",
        "/api/devices",
      ),
    });
  });
  await page.goto(publicURL + "/network-status.html");
  await expect(
    page.getByText("Network status temporarily unavailable.", { exact: false }),
  ).toBeVisible();
  expect(privateRequests).toEqual([]);
});

test("legacy public widget identifies stale approved information as unknown", async ({ page }) => {
  await page.route("**/public/network-status/v1", route => route.fulfill({json:{
    api_version:"1", overall_status:"all_systems_operational", last_public_update:new Date(Date.now()-172800000).toISOString(),
    basis:"administrator-approved announcements",incidents:[],maintenance:[{id:"11111111-1111-4111-8111-111111111111",title:"Approved synthetic maintenance",description:"An approved scheduled window awaiting an operator update.",status:"all_systems_operational",areas:["Synthetic community"],starts_at:new Date(Date.now()-3600000).toISOString(),ends_at:new Date(Date.now()+3600000).toISOString(),estimated_restoration_at:null,updated_at:new Date().toISOString()}],resolved:[],next_history_offset:null,
  }}));
  await page.goto(publicURL+"/network-status.html");
  await expect(page.getByRole('heading',{name:'Status unknown — stale approved information'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'All systems operational',exact:true})).toHaveCount(0);
  await expect(page.getByText('Scheduled maintenance',{exact:true})).toBeVisible();
});
