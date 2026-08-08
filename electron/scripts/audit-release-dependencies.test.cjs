const assert = require("node:assert/strict");
const test = require("node:test");

const {
  evaluateAuditReports,
  parseAuditReport,
} = require("./audit-release-dependencies.cjs");

function fixture(counts, packageNames = []) {
  return JSON.stringify({
    auditReportVersion: 2,
    vulnerabilities: Object.fromEntries(
      packageNames.map((packageName) => [
        packageName,
        { name: packageName, severity: "high" },
      ])
    ),
    metadata: {
      vulnerabilities: {
        info: 0,
        low: 0,
        moderate: 0,
        high: 0,
        critical: 0,
        ...counts,
      },
    },
  });
}

test("passes when findings are confined to dev/build dependencies", () => {
  const production = parseAuditReport(
    fixture({ total: 0 }),
    "production fixture"
  );
  const full = parseAuditReport(
    fixture({ high: 16, total: 16 }, [
      "app-builder-lib",
      "brace-expansion",
      "electron-builder",
    ]),
    "full fixture"
  );

  const result = evaluateAuditReports({ production, full });

  assert.equal(result.passed, true);
  assert.equal(result.production.counts.total, 0);
  assert.equal(result.devBuild.counts.high, 16);
  assert.deepEqual(result.devBuild.packages, [
    "app-builder-lib",
    "brace-expansion",
    "electron-builder",
  ]);
});

test("fails when any production dependency vulnerability is present", () => {
  const production = parseAuditReport(
    fixture({ low: 1, total: 1 }, ["runtime-package"])
  );
  const full = parseAuditReport(
    fixture({ low: 1, high: 16, total: 17 }, [
      "brace-expansion",
      "runtime-package",
    ])
  );

  const result = evaluateAuditReports({ production, full });

  assert.equal(result.passed, false);
  assert.equal(result.production.counts.low, 1);
  assert.equal(result.devBuild.counts.high, 16);
  assert.deepEqual(result.devBuild.packages, ["brace-expansion"]);
});

test("rejects malformed or internally inconsistent npm audit JSON", () => {
  assert.throws(
    () => parseAuditReport("not-json", "broken fixture"),
    /did not return valid JSON/
  );
  assert.throws(
    () => parseAuditReport(JSON.stringify({}), "empty fixture"),
    /missing metadata\.vulnerabilities/
  );
  assert.throws(
    () => parseAuditReport(fixture({ high: 2, total: 1 }), "bad total"),
    /does not match severity sum/
  );
});
