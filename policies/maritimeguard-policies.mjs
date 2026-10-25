import { customPolicies, allow, deny } from "failproofai";
import { readFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";

// The validator CLI writes .maritimeguard/ledger.json (input-hash -> ran, plus the latest decision/stdout).
// These policies only read it, so they depend on nothing beyond documented hook fields.
const ledgerPath = (ctx) => `${ctx.session?.cwd ?? "."}/.maritimeguard/ledger.json`;
const load = (ctx) => {
  try { return JSON.parse(readFileSync(ledgerPath(ctx), "utf8")); } catch { return { runs: {}, last: null }; }
};
const cmd = (ctx) => (ctx.toolName === "Bash" ? ctx.toolInput?.command ?? "" : "");

function inputHash(ctx, command) {
  const m = command.match(/maritimeguard\.validate\s+["']?([^\s"']+)/);
  if (!m) return null;
  const p = m[1].match(/^([A-Za-z]:)?[\\/]/) ? m[1] : `${ctx.session?.cwd ?? "."}/${m[1]}`;
  return existsSync(p) ? createHash("sha256").update(readFileSync(p)).digest("hex") : null;
}

customPolicies.add({
  name: "no-unsafe-retry",
  description: "Validator may run once per input hash; task input may not be edited after validation",
  match: { events: ["PreToolUse"] },
  fn: async (ctx) => {
    const h = inputHash(ctx, cmd(ctx));
    if (h && load(ctx).runs[h]) return deny("Validator already ran on this input. Use its decision; do not retry.");
    if (ctx.toolName === "Write" || ctx.toolName === "Edit") {
      const p = ctx.toolInput?.file_path ?? "";
      if (load(ctx).last && /(task|input)[^\\/]*\.json$/i.test(p))
        return deny("Do not edit the task input after validation. Use the validator's decision.");
    }
    return allow();
  },
});

customPolicies.add({
  name: "no-unverified-authorization",
  description: "Deny authorization unless the latest validator decision is APPROVE and route_hash matches",
  match: { events: ["PreToolUse"] },
  fn: async (ctx) => {
    const c = cmd(ctx);
    const writing = (ctx.toolName === "Write" || ctx.toolName === "Edit") &&
      /authorized_route\.json$/.test(ctx.toolInput?.file_path ?? "");
    const calling = /fleet_system(\.py)?\s+authorize/.test(c);
    if (!writing && !calling) return allow();
    const last = load(ctx).last;
    if (!last || last.decision !== "APPROVE" || !last.route_hash)
      return deny("Run the validator and use its decision.");
    if (calling && c.trim().split(/\s+/).pop() !== last.route_hash)
      return deny("route_hash does not match the validator's APPROVE hash. Run the validator and use its decision.");
    if (writing && !String(ctx.toolInput?.content ?? "").includes(last.route_hash))
      return deny("Authorization content must carry the validator's route_hash. Run the validator and use its decision.");
    return allow();
  },
});

customPolicies.add({
  name: "validator-is-source-of-truth",
  description: "Deny final answers whose decision differs from the validator's stdout",
  match: { events: ["Stop"] },
  fn: async (ctx) => {
    const last = load(ctx).last;
    const tp = ctx.session?.transcriptPath;
    if (!last || !tp || !existsSync(tp)) return allow();
    let text = "";
    for (const line of readFileSync(tp, "utf8").split("\n")) {
      try {
        const e = JSON.parse(line);
        if (e.type === "assistant") {
          const parts = e.message?.content;
          const t = Array.isArray(parts) ? parts.filter((p) => p.type === "text").map((p) => p.text).join("") : "";
          if (t) text = t;
        }
      } catch {}
    }
    const m = text.match(/"decision"\s*:\s*"(APPROVE|REJECT|ESCALATE)"/);
    if (m && m[1] !== last.decision)
      return deny(`Final decision must equal the validator's (${last.decision}). Output its JSON verbatim.`);
    return allow();
  },
});
