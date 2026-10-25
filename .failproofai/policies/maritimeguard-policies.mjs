import { customPolicies, allow, deny } from "failproofai";
import { readFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";

// Session state is derived from the Claude Code transcript-free source of truth: the ledger file the
// policies maintain under .maritimeguard/ (written when a validator run is observed in PostToolUse).
const LEDGER = ".maritimeguard/ledger.json";
const load = () => (existsSync(LEDGER) ? JSON.parse(readFileSync(LEDGER, "utf8")) : { runs: {}, last: null });
const sha = (s) => createHash("sha256").update(s).digest("hex");
const cmd = (ctx) => (ctx.toolName === "Bash" ? ctx.toolInput?.command ?? "" : "");

// Decision/ledger recording happens in the validator wrapper-free way: we read the input file the
// agent is about to validate and key runs by its content hash.
function inputHash(command) {
  const m = command.match(/maritimeguard\.validate\s+(\S+)/);
  if (!m || !existsSync(m[1])) return null;
  return sha(readFileSync(m[1], "utf8"));
}

customPolicies.add({
  name: "no-unsafe-retry",
  description: "Validator may run once per input hash; task input may not be edited after validation",
  match: { events: ["PreToolUse"] },
  fn: async (ctx) => {
    const c = cmd(ctx);
    const h = inputHash(c);
    if (h) {
      const led = load();
      if (led.runs[h]) return deny("Validator already ran on this input. Use its decision; do not retry.");
    }
    if ((ctx.toolName === "Write" || ctx.toolName === "Edit") && existsSync(LEDGER)) {
      const p = ctx.toolInput?.file_path ?? "";
      if (/task[^\\/]*\.json$/i.test(p) || /input[^\\/]*\.json$/i.test(p))
        return deny("Do not edit the task input after validation. Use the validator's decision.");
    }
    return allow();
  },
});

customPolicies.add({
  name: "no-unverified-authorization",
  description: "Deny authorization writes/calls unless the latest validator decision is APPROVE with a matching route_hash",
  match: { events: ["PreToolUse"] },
  fn: async (ctx) => {
    const c = cmd(ctx);
    const writing = (ctx.toolName === "Write" || ctx.toolName === "Edit") &&
      /authorized_route\.json$/.test(ctx.toolInput?.file_path ?? "");
    const calling = /maritimeguard\.fleet_system\s+authorize/.test(c) || /fleet_system\.py\s+authorize/.test(c);
    if (!writing && !calling) return allow();
    const last = load().last;
    if (!last || last.decision !== "APPROVE" || !last.route_hash)
      return deny("Run the validator and use its decision.");
    if (calling) {
      const parts = c.trim().split(/\s+/);
      if (parts[parts.length - 1] !== last.route_hash)
        return deny("route_hash does not match the validator's APPROVE hash. Run the validator and use its decision.");
    }
    return allow();
  },
});

// Records validator output (PostToolUse) so the other policies have the session's source of truth.
customPolicies.add({
  name: "record-validator-output",
  description: "Remember the latest validator stdout and the input hash it was computed for",
  match: { events: ["PostToolUse"] },
  fn: async (ctx) => {
    const c = cmd(ctx);
    const h = inputHash(c);
    if (!h) return allow();
    try {
      const raw = typeof ctx.toolResult === "string" ? ctx.toolResult : JSON.stringify(ctx.toolResult ?? "");
      const out = JSON.parse(raw.trim().split("\n").filter((l) => l.startsWith("{")).pop());
      const { mkdirSync, writeFileSync } = await import("node:fs");
      const led = load();
      led.runs[h] = true;
      led.last = { decision: out.decision, route_hash: out.route_hash, stdout: JSON.stringify(out) };
      mkdirSync(".maritimeguard", { recursive: true });
      writeFileSync(LEDGER, JSON.stringify(led));
    } catch {}
    return allow();
  },
});

customPolicies.add({
  name: "validator-is-source-of-truth",
  description: "Deny a final answer whose decision differs from the validator's stdout",
  match: { events: ["Stop"] },
  fn: async (ctx) => {
    const last = load().last;
    if (!last) return allow();
    const msg = String(ctx.lastAssistantMessage ?? ctx.stopMessage ?? "");
    const m = msg.match(/"decision"\s*:\s*"(APPROVE|REJECT|ESCALATE)"/);
    if (m && m[1] !== last.decision)
      return deny(`Final decision must equal the validator's (${last.decision}). Output its JSON verbatim.`);
    return allow();
  },
});
