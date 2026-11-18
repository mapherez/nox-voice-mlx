import assert from "node:assert/strict";
import test from "node:test";
import { assessAudit, exception, runAudit } from "./audit-security.mjs";

const beforeExpiry = new Date("2026-11-18T00:00:00Z");

function fixture() {
  const advisory = {
    name: "picomatch",
    dependency: "picomatch",
    url: exception.url,
    severity: "high",
  };
  const entry = (name, via) => ({
    name,
    via,
    severity: "high",
    nodes: [`node_modules/${name}`],
  });
  // Match npm's cyclic Vitest parent graph as well as the direct advisory.
  const report = {
    auditReportVersion: 2,
    vulnerabilities: {
      picomatch: entry("picomatch", [advisory]),
      vitest: entry("vitest", ["picomatch", "browser"]),
      browser: entry("browser", ["vitest"]),
    },
    metadata: {
      vulnerabilities: {
        info: 0,
        low: 0,
        moderate: 0,
        high: 3,
        critical: 0,
        total: 3,
      },
    },
  };
  const lock = {
    lockfileVersion: 3,
    packages: Object.fromEntries(
      Object.keys(report.vulnerabilities).map((name) => [
        `node_modules/${name}`,
        { dev: true, version: name === "picomatch" ? "4.0.7" : "5.1.0" },
      ]),
    ),
  };
  return { report, lock, advisory };
}

test("only the exact dev-only advisory and its affected parents are excepted", () => {
  const { report, lock } = fixture();
  const result = assessAudit(report, lock, beforeExpiry);
  assert.deepEqual(result.ignored, ["picomatch", "vitest", "browser"]);
  assert.deepEqual(result.blocking, []);
});

test("exceptions stop applying exactly at expiry", () => {
  const { report, lock } = fixture();
  for (const now of [
    new Date(exception.expires),
    new Date("2026-12-03T00:00:00Z"),
  ]) {
    assert.deepEqual(assessAudit(report, lock, now).blocking, [
      "picomatch",
      "vitest",
      "browser",
    ]);
  }
});

test("production, unknown and changed picomatch versions cannot use the exception", () => {
  for (const edit of [
    (lock) => {
      lock.packages["node_modules/picomatch"].dev = false;
    },
    (lock) => {
      delete lock.packages["node_modules/picomatch"];
    },
    (lock) => {
      lock.packages["node_modules/picomatch"].version = "4.0.8";
    },
  ]) {
    const { report, lock } = fixture();
    edit(lock);
    assert.deepEqual(assessAudit(report, lock, beforeExpiry).blocking, [
      "picomatch",
      "vitest",
      "browser",
    ]);
  }
  const { report, lock } = fixture();
  lock.packages["node_modules/vitest"].dev = false;
  assert.deepEqual(assessAudit(report, lock, beforeExpiry).blocking, [
    "vitest",
    "browser",
  ]);
});

test("a second advisory in picomatch or its consumers still blocks", () => {
  for (const name of ["picomatch", "vitest"]) {
    const { report, lock, advisory } = fixture();
    report.vulnerabilities[name].via.push({
      ...advisory,
      name,
      dependency: name,
      url: "https://github.com/advisories/GHSA-other",
    });
    const result = assessAudit(report, lock, beforeExpiry);
    assert.ok(result.blocking.includes(name));
    assert.ok(result.blocking.includes("browser"));
  }
});

test("critical advisories cannot use the high-severity exception", () => {
  const { report, lock, advisory } = fixture();
  advisory.severity = "critical";
  assert.deepEqual(assessAudit(report, lock, beforeExpiry).blocking, [
    "picomatch",
    "vitest",
    "browser",
  ]);
  const second = fixture();
  second.report.vulnerabilities.vitest.severity = "critical";
  second.report.metadata.vulnerabilities.high--;
  second.report.metadata.vulnerabilities.critical++;
  assert.deepEqual(
    assessAudit(second.report, second.lock, beforeExpiry).blocking,
    ["vitest", "browser"],
  );
});

test("unrelated high and critical advisories still block in any dependency", () => {
  for (const severity of ["high", "critical"]) {
    const { report, lock } = fixture();
    report.vulnerabilities.other = {
      name: "other",
      severity,
      nodes: ["node_modules/other"],
      via: [
        {
          name: "other",
          dependency: "other",
          url: "https://github.com/advisories/GHSA-other",
          severity,
        },
      ],
    };
    report.metadata.vulnerabilities[severity]++;
    report.metadata.vulnerabilities.total++;
    lock.packages["node_modules/other"] = { version: "1.0.0", dev: true };
    assert.deepEqual(assessAudit(report, lock, beforeExpiry).blocking, [
      "other",
    ]);
  }
});

test("lower-severity advisories stay visible without changing the high threshold", () => {
  const { report, lock } = fixture();
  report.vulnerabilities.picomatch.severity = "moderate";
  report.metadata.vulnerabilities.high--;
  report.metadata.vulnerabilities.moderate++;
  assert.deepEqual(assessAudit(report, lock, beforeExpiry).warnings, [
    "picomatch",
  ]);
});

test("a clean audit passes even after the exception expires", () => {
  const { report, lock } = fixture();
  report.vulnerabilities = {};
  report.metadata.vulnerabilities = {
    info: 0,
    low: 0,
    moderate: 0,
    high: 0,
    critical: 0,
    total: 0,
  };
  assert.deepEqual(
    assessAudit(report, lock, new Date("2027-01-01")).blocking,
    [],
  );
});

test("incomplete, unsupported or errored audit reports fail closed", () => {
  for (const edit of [
    (report) => {
      report.error = { message: "registry unavailable" };
    },
    (report) => {
      report.auditReportVersion = 1;
    },
    (report) => {
      delete report.metadata;
    },
    (report) => {
      report.vulnerabilities = {};
    },
    (report) => {
      report.vulnerabilities.vitest.via = ["missing"];
    },
    (report) => {
      report.vulnerabilities.picomatch.via = [];
    },
    (report) => {
      report.vulnerabilities.picomatch.nodes = [];
    },
  ]) {
    const { report, lock } = fixture();
    edit(report);
    assert.throws(() => assessAudit(report, lock, beforeExpiry));
  }
  const { report, lock } = fixture();
  report.vulnerabilities.picomatch.via = ["vitest"];
  assert.equal(assessAudit(report, lock, beforeExpiry).ignored.length, 0);
});

test("registry failures and invalid CLI output cannot produce a successful audit", () => {
  for (const result of [
    { status: 2, stderr: "network failed" },
    { status: null, error: new Error("npm missing") },
    { status: 1, stdout: "not json" },
    {
      status: 1,
      stdout: JSON.stringify({ error: { message: "registry unavailable" } }),
    },
  ]) {
    assert.throws(() => runAudit({ now: beforeExpiry, execute: () => result }));
  }
});

test("the CLI passes the scoped exception and returns failure for other or expired advisories", (t) => {
  t.mock.method(console, "warn", () => {});
  t.mock.method(console, "error", () => {});
  t.mock.method(console, "log", () => {});
  const { report } = fixture();
  delete report.vulnerabilities.vitest;
  delete report.vulnerabilities.browser;
  report.metadata.vulnerabilities.high =
    report.metadata.vulnerabilities.total = 1;
  const execute = () => ({ status: 1, stdout: JSON.stringify(report) });
  assert.equal(runAudit({ execute, now: beforeExpiry }), 0);
  assert.equal(runAudit({ execute, now: new Date(exception.expires) }), 1);
  report.vulnerabilities.picomatch.via[0].url =
    "https://github.com/advisories/GHSA-other";
  assert.equal(runAudit({ execute, now: beforeExpiry }), 1);

  report.vulnerabilities = {};
  report.metadata.vulnerabilities.high =
    report.metadata.vulnerabilities.total = 0;
  assert.throws(() => runAudit({ execute, now: beforeExpiry }), /exit status/);
  assert.equal(
    runAudit({
      execute: () => ({ status: 0, stdout: JSON.stringify(report) }),
      now: beforeExpiry,
    }),
    0,
  );
});
