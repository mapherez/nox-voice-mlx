import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const exception = Object.freeze({
  package: "picomatch",
  version: "4.0.7",
  url: "https://github.com/advisories/GHSA-fc5q-39xc-vjpf",
  expires: "2026-12-02T00:00:00Z",
});

const severities = new Set(["info", "low", "moderate", "high", "critical"]);
const blocksCI = (severity) => severity === "high" || severity === "critical";

export function assessAudit(report, lock, now = new Date()) {
  if (
    report?.error ||
    report?.auditReportVersion !== 2 ||
    !report.vulnerabilities ||
    typeof report.vulnerabilities !== "object" ||
    Array.isArray(report.vulnerabilities) ||
    lock?.lockfileVersion !== 3 ||
    !lock.packages ||
    !Number.isFinite(now.getTime())
  ) {
    throw new Error(
      "Invalid npm audit report, lockfile or audit date; refusing to allow exceptions.",
    );
  }
  const entries = Object.entries(report.vulnerabilities);
  const counts = Object.fromEntries(
    [...severities].map((severity) => [severity, 0]),
  );
  for (const [name, item] of entries) {
    if (
      item?.name !== name ||
      !severities.has(item.severity) ||
      !Array.isArray(item.nodes) ||
      item.nodes.length === 0 ||
      item.nodes.some((node) => typeof node !== "string") ||
      !Array.isArray(item.via) ||
      item.via.length === 0 ||
      item.via.some((via) =>
        typeof via === "string"
          ? !Object.hasOwn(report.vulnerabilities, via)
          : !via ||
            via.name !== name ||
            via.dependency !== name ||
            typeof via.url !== "string" ||
            !severities.has(via.severity),
      )
    ) {
      throw new Error(`Incomplete audit dependency information for ${name}.`);
    }
    counts[item.severity]++;
  }
  const summary = report.metadata?.vulnerabilities;
  if (
    summary?.total !== entries.length ||
    [...severities].some((severity) => summary[severity] !== counts[severity])
  ) {
    throw new Error("Audit totals do not match the reported vulnerabilities.");
  }

  const ignored = [],
    blocking = [],
    warnings = [];
  const active = now.getTime() < Date.parse(exception.expires);
  for (const [name, item] of entries) {
    if (!blocksCI(item.severity)) {
      warnings.push(name);
      continue;
    }
    const seen = new Set(),
      advisories = [];
    let devOnly = true;
    const visit = (dependency) => {
      if (seen.has(dependency)) return;
      seen.add(dependency);
      const entry = report.vulnerabilities[dependency];
      if (entry.severity !== "high") devOnly = false;
      for (const node of entry.nodes) {
        const installed = lock.packages[node];
        if (
          installed?.dev !== true ||
          (dependency === exception.package &&
            installed.version !== exception.version)
        )
          devOnly = false;
      }
      for (const via of entry.via) {
        if (typeof via === "string") visit(via);
        else advisories.push(via);
      }
    };
    visit(name);
    // npm also flags parent packages. Ignore them only if every underlying
    // advisory is this exact exception and the entire affected graph is dev-only.
    const allowed =
      active &&
      devOnly &&
      advisories.length > 0 &&
      advisories.every(
        (via) =>
          via.url === exception.url &&
          via.name === exception.package &&
          via.dependency === exception.package &&
          via.severity === "high",
      );
    (allowed ? ignored : blocking).push(name);
  }
  return { ignored, blocking, warnings, active };
}

export function runAudit({ execute = spawnSync, now = new Date() } = {}) {
  const frontend = fileURLToPath(new URL("../frontend/", import.meta.url));
  const args = ["audit", "--json", "--audit-level=high"];
  const npmCLI = process.env.npm_execpath;
  const result = execute(
    npmCLI ? process.execPath : "npm",
    npmCLI ? [npmCLI, ...args] : args,
    {
      cwd: frontend,
      encoding: "utf8",
      maxBuffer: 16 * 1024 * 1024,
      shell: !npmCLI && process.platform === "win32",
    },
  );
  if (result.error || ![0, 1].includes(result.status)) {
    throw new Error(
      `npm audit failed: ${result.error?.message ?? result.stderr ?? result.status}`,
    );
  }
  const report = JSON.parse(result.stdout);
  const lock = JSON.parse(
    readFileSync(new URL("../frontend/package-lock.json", import.meta.url)),
  );
  const assessment = assessAudit(report, lock, now);
  const hasHigh = Object.values(report.vulnerabilities).some((item) =>
    blocksCI(item.severity),
  );
  if ((result.status === 1) !== hasHigh) {
    throw new Error("npm audit exit status does not match its report.");
  }
  if (assessment.ignored.length) {
    console.warn(
      `Temporary dev-only exception: ${exception.url}; picomatch ${exception.version}; expires ${exception.expires}.`,
    );
    console.warn(`Affected packages: ${assessment.ignored.join(", ")}`);
  }
  if (assessment.warnings.length)
    console.warn(`Non-blocking advisories: ${assessment.warnings.join(", ")}`);
  if (assessment.blocking.length) {
    if (!assessment.active)
      console.error(`The picomatch exception expired on ${exception.expires}.`);
    console.error(JSON.stringify(report, null, 2));
    console.error(`Blocking advisories: ${assessment.blocking.join(", ")}`);
    return 1;
  }
  console.log("Security audit passed: no unexcepted high/critical advisories.");
  return 0;
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    process.exitCode = runAudit();
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
