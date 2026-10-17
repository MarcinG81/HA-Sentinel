// Replay card for the Autopilot tab (spec 11.4). A replay is not a simulation of the home:
// it never calls services, never models device responses, and missing data is "unknown".

import type { Ctx } from "./ctx.ts";
import { h } from "./dom.ts";
import type { WsError } from "./types.ts";

interface Occurrence {
  time: string;
  automation: string;
  entity_id: string;
  from: string | null;
  to: string | null;
  conditions: "true" | "false" | "unknown";
  planned_actions: { pointer: string; service?: string; unknown_branch?: boolean; stopped?: string }[];
  plan_complete: boolean;
}

interface Report {
  occurrences: Occurrence[];
  unsupported: { automation: string; pointer: string; reason: string }[];
  supported_nodes: number;
  total_nodes: number;
  missing_initial: string[];
  truncated: boolean;
  events: number;
  events_truncated: boolean;
  entities: string[];
  window: { start: string; end: string };
  interpreter_version: number;
}

const TRI_CLASS = { true: "ok", false: "status", unknown: "sev-warning" } as const;

export function renderReplayCard(ctx: Ctx, snapshotId: string, available: boolean): HTMLElement {
  const { t } = ctx;
  const out = h("div", { role: "status", "aria-live": "polite" });
  const hours = h("select", { id: "rp-hours" }, [1, 6, 12, 24].map((n) => h("option", { value: String(n), selected: n === 6 }, t("replay.hours").replace("{n}", String(n))))) as HTMLSelectElement;
  const run = h("button", { class: "secondary", disabled: !available }, t("replay.run")) as HTMLButtonElement;
  run.addEventListener("click", async () => {
    run.disabled = true;
    out.replaceChildren(h("p", { class: "muted" }, t("loading")));
    try {
      const rep = await ctx.ws<Report>({ type: "ha_sentinel/autopilot/replay", snapshot_id: snapshotId, hours: Number(hours.value) });
      out.replaceChildren(renderReport(ctx, rep));
    } catch (err) {
      const we = err as WsError;
      out.replaceChildren(h("p", { class: "banner error" }, we.details?.reason ? t(`replay.reason.${we.details.reason}`) : ctx.errorText(we)));
    }
    run.disabled = false;
  });
  return h(
    "section",
    { class: "card", "aria-labelledby": "h-rp" },
    h("h2", { id: "h-rp" }, t("replay.title")),
    h("div", { class: "banner", role: "note" }, t("replay.disclaimer")),
    !available ? h("p", { class: "muted" }, t("replay.reason.recorder_unavailable")) : null,
    h("div", { class: "row" }, h("label", { for: "rp-hours", class: "sr-only" }, t("replay.window")), hours, run),
    out,
  );
}

function renderReport(ctx: Ctx, rep: Report): HTMLElement {
  const { t } = ctx;
  const pct = rep.total_nodes ? Math.round((rep.supported_nodes / rep.total_nodes) * 100) : 100;
  return h(
    "div",
    { class: "stack" },
    h("dl", {},
      h("dt", {}, t("replay.window")), h("dd", {}, `${ctx.date(rep.window.start, true)} → ${ctx.date(rep.window.end, true)}`),
      h("dt", {}, t("replay.events")), h("dd", {}, `${rep.events}${rep.events_truncated ? ` (${t("replay.truncated")})` : ""} · ${rep.entities.length} ${t("replay.entities")}`),
      h("dt", {}, t("replay.coverage")), h("dd", {}, `${rep.supported_nodes} / ${rep.total_nodes} ${t("replay.triggers")} (${pct}%) · ${t("replay.interpreter")} v${rep.interpreter_version}`),
      rep.missing_initial.length ? [h("dt", {}, t("replay.missing_initial")), h("dd", {}, rep.missing_initial.join(", "))] : null),
    rep.occurrences.length === 0
      ? h("p", { class: "muted" }, t("replay.none"))
      : h("ul", { class: "list" }, rep.occurrences.map((o) => h("li", {}, h("div", { class: "item" },
          h("div", { class: "row spread" }, h("span", { class: "title" }, `${o.automation}`), h("span", { class: `badge ${TRI_CLASS[o.conditions]}` }, t(`replay.would.${o.conditions}`))),
          h("div", { class: "sub" }, `${ctx.date(o.time, true)} · ${o.entity_id}: ${o.from ?? "?"} → ${o.to ?? "?"}`),
          o.planned_actions.length
            ? h("div", { class: "sub" }, `${t("replay.planned")}: `, o.planned_actions.map((a) => a.service ?? (a.unknown_branch ? t("replay.unknown_branch") : t("replay.stopped"))).join(" → "), o.plan_complete ? "" : ` (${t("replay.incomplete")})`)
            : null)))),
    rep.unsupported.length
      ? h("details", {}, h("summary", {}, `${t("replay.unsupported")} (${rep.unsupported.length})`),
          h("ul", {}, rep.unsupported.map((u) => h("li", {}, `${u.automation}: `, h("code", {}, u.pointer), ` — ${u.reason}`))))
      : null,
  );
}
