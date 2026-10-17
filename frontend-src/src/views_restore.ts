// Managed files (type M): register, sync and restore with preview (spec 10.3-10.4, 14.1).
// The restore button only exists on the preview, next to the exact diff and scope.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { Capability, JobInfo, RestorePreview, WsError } from "./types.ts";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function managedCapability(ctx: Ctx): Promise<Capability | undefined> {
  const { capabilities } = await ctx.ws<{ capabilities: Capability[] }>({ type: "ha_sentinel/capabilities" });
  return capabilities.find((c) => c.capability_id === "managed_yaml.restore");
}

export function renderRegisterCard(ctx: Ctx, cap: Capability | undefined): HTMLElement {
  const { t, state } = ctx;
  if (cap?.status !== "supported") {
    return h(
      "section",
      { class: "card", "aria-labelledby": "h-mreg" },
      h("h2", { id: "h-mreg" }, t("managed.title")),
      h("p", { class: "muted" }, t("managed.disabled")),
      cap?.reason_code ? h("p", { class: "muted" }, h("code", {}, cap.reason_code)) : null,
    );
  }
  const d = state.managedDraft;
  const path = h("input", { id: "m-path", type: "text", value: d.path, oninput: (e: Event) => { d.path = (e.target as HTMLInputElement).value; } });
  const label = h("input", { id: "m-label", type: "text", maxlength: "120", value: d.label, oninput: (e: Event) => { d.label = (e.target as HTMLInputElement).value; } });
  const own = h("input", { id: "m-own", type: "checkbox", checked: d.exclusive, onchange: (e: Event) => { d.exclusive = (e.target as HTMLInputElement).checked; } });
  const status = h("div", { role: "status" });
  const form = h(
    "form",
    {
      onsubmit: async (e: Event) => {
        e.preventDefault();
        try {
          const res = await ctx.ws<{ resource_id: string }>({
            type: "ha_sentinel/managed/register",
            relative_path: d.path.trim(),
            label: d.label.trim() || "Managed",
            exclusive_owner_confirmed: d.exclusive,
            request_key: uuid4(),
          });
          state.resourceId = res.resource_id;
          ctx.rerender();
        } catch (err) {
          const we = err as WsError;
          status.replaceChildren(h("p", { class: "banner error" }, t(`managed.reason.${we.details?.reason ?? "generic"}`)));
        }
      },
    },
    h("div", { class: "banner", role: "note" }, t("managed.include_note")),
    h("div", { class: "grid2" },
      h("div", {}, h("label", { for: "m-path" }, t("managed.path")), path),
      h("div", {}, h("label", { for: "m-label" }, t("import.label")), label)),
    h("label", { class: "row check", for: "m-own" }, own, t("managed.exclusive")),
    status,
    h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, t("managed.register"))),
  );
  return h("section", { class: "card", "aria-labelledby": "h-mreg" }, h("h2", { id: "h-mreg" }, t("managed.title")), form);
}

export async function syncManaged(ctx: Ctx, resourceId: string): Promise<void> {
  try {
    const res = await ctx.ws<{ deduplicated: boolean }>({ type: "ha_sentinel/managed/sync", resource_id: resourceId, request_key: uuid4() });
    ctx.toast(ctx.t(res.deduplicated ? "managed.sync_same" : "managed.sync_new"));
  } catch (err) {
    ctx.toast(ctx.errorText(err as WsError));
  }
  ctx.rerender();
}

export async function openRestorePreview(ctx: Ctx, resourceId: string, snapshotId: string): Promise<void> {
  try {
    ctx.state.restore = await ctx.ws<RestorePreview>({ type: "ha_sentinel/changes/preview", resource_id: resourceId, snapshot_id: snapshotId });
  } catch (err) {
    const we = err as WsError;
    ctx.toast(we.details?.reason === "no_change" ? ctx.t("restore.no_change") : ctx.errorText(we));
  }
  ctx.rerender();
}

export function renderRestorePreview(ctx: Ctx): HTMLElement {
  const { t, state } = ctx;
  const p = state.restore!;
  const fmt = (v: unknown) => (v === undefined ? "" : JSON.stringify(v));
  const minutesLeft = Math.max(0, Math.round((new Date(p.expires_at).getTime() - Date.now()) / 60000));
  const result = h("div", { role: "status", "aria-live": "polite" });
  const apply = h("button", { class: "primary danger" }, t("restore.apply")) as HTMLButtonElement;
  apply.addEventListener("click", async () => {
    apply.disabled = true;
    try {
      const started = await ctx.ws<{ job_id: string }>({ type: "ha_sentinel/changes/apply", plan_id: p.plan_id, plan_hash: p.plan_hash, request_key: uuid4() });
      result.replaceChildren(h("p", {}, t("job.running")));
      for (let i = 0; i < 60; i++) {
        const job = await ctx.ws<JobInfo>({ type: "ha_sentinel/jobs/get", job_id: started.job_id });
        if (!["queued", "running"].includes(job.state)) {
          const planState = (job.result as { plan_state?: string } | null)?.plan_state ?? job.error_code ?? job.state;
          result.replaceChildren(h("div", { class: `banner ${planState === "applied" ? "ok" : "error"}` }, h("div", {}, h("strong", {}, t(`plan.${planState}`)), h("div", {}, t(`plan.${planState}.hint`)))));
          return;
        }
        await sleep(700);
      }
    } catch (err) {
      const we = err as WsError;
      result.replaceChildren(h("p", { class: "banner error" }, we.code === "plan_expired" ? t("plan.expired.hint") : ctx.errorText(we)));
    }
  });
  const lint = Object.entries(p.validation.lint);
  return h(
    "div",
    { class: "stack" },
    h("button", { class: "link", onclick: () => { state.restore = null; ctx.rerender(); } }, `← ${t("history.back_snapshots")}`),
    h(
      "section",
      { class: "card" },
      h("h2", {}, t("restore.title")),
      h("div", { class: "banner", role: "note" }, t("restore.physical_note")),
      h(
        "dl",
        {},
        h("dt", {}, t("restore.expires")),
        h("dd", {}, `${ctx.date(p.expires_at, true)} (${t("restore.minutes").replace("{n}", String(minutesLeft))})`),
        h("dt", {}, t("restore.plan")),
        h("dd", {}, h("code", {}, p.plan_hash.slice(7, 19))),
        h("dt", {}, t("restore.reload")),
        h("dd", {}, t(p.reload ? "restore.reload_on" : "restore.reload_off")),
        h("dt", {}, t("restore.automations")),
        h("dd", {}, p.impact.automations.join(", ") || "—"),
        h("dt", {}, t("restore.entities")),
        h("dd", {}, p.impact.entities.length ? p.impact.entities.join(", ") : "—"),
        h("dt", {}, t("restore.validation")),
        h("dd", {}, lint.length ? lint.map(([k, n]) => `${t(`level.${k}`)}: ${n}`).join(" · ") : t("autopilot.clean"),
          ` · ${t("autopilot.completeness")} ${Math.round(p.validation.completeness * 100)}%`),
      ),
      h("div", { class: "actions" }, apply),
      result,
    ),
    h(
      "section",
      { class: "card" },
      h("h2", {}, t("diff.semantic_title")),
      p.diff.semantic.length === 0
        ? h("p", { class: "muted" }, t("diff.no_semantic"))
        : h("table", { class: "diff" },
            h("tbody", {}, p.diff.semantic.map((c) => h("tr", {},
              h("td", {}, h("span", { class: `badge op-${c.op}` }, t(`diff.op.${c.op}`))),
              h("td", {}, h("code", {}, c.path)),
              h("td", {}, c.op !== "add" ? h("div", { class: "old" }, h("code", {}, `− ${fmt(c.old)}`)) : null, c.op !== "remove" ? h("div", { class: "new" }, h("code", {}, `+ ${fmt(c.new)}`)) : null))))),
    ),
    h(
      "section",
      { class: "card" },
      h("h2", {}, t("diff.textual_title")),
      h("pre", { class: "code", tabindex: "0" }, p.diff.textual.map((line) =>
        h("span", { class: line.startsWith("+") && !line.startsWith("+++") ? "line add" : line.startsWith("-") && !line.startsWith("---") ? "line del" : "line" }, line, "\n"))),
    ),
  );
}
