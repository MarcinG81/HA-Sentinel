// Cleanup report (cleanup window): HA objects inactive longer than the window. Report only;
// each row links to the HA page where the admin decides. Sentinel never removes HA objects.

import type { Ctx } from "./ctx.ts";
import { h } from "./dom.ts";

interface CleanupItem {
  kind: "entity" | "device";
  id: string;
  name: string | null;
  reason: string;
  since: string | null;
  days: number | null;
  entities?: number;
  link: string;
}

interface CleanupReport {
  window_days: number;
  items: CleanupItem[];
  truncated: boolean;
  last_purge: { at: string; entities: number; devices: number } | null;
}

export async function renderCleanupCard(ctx: Ctx, link: (path: string, label: string) => HTMLElement): Promise<HTMLElement> {
  const { t } = ctx;
  const r = await ctx.ws<CleanupReport>({ type: "ha_sentinel/cleanup/report" });
  const row = (i: CleanupItem) => h(
    "tr",
    {},
    h("th", { scope: "row" }, i.name ?? i.id, h("div", { class: "muted small-text" }, i.kind === "entity" ? h("code", {}, i.id) : t("node.device"))),
    h("td", {}, t(`cleanup.reason.${i.reason}`), i.kind === "device" && i.entities !== undefined ? h("div", { class: "muted small-text" }, t("health.entities").replace("{n}", String(i.entities))) : null),
    h("td", {}, i.days !== null ? t("cleanup.days").replace("{n}", String(i.days)) : "—"),
    h("td", {}, link(i.link, t("cleanup.open"))),
  );
  return h(
    "section",
    { class: "card", "aria-labelledby": "h-clean" },
    h("h2", { id: "h-clean" }, t("cleanup.title")),
    h("p", { class: "muted" }, t("cleanup.hint").replace("{n}", String(r.window_days))),
    r.items.length === 0
      ? h("p", { class: "muted" }, t("cleanup.none").replace("{n}", String(r.window_days)))
      : h("div", { class: "scroll", tabindex: "0", role: "region", "aria-labelledby": "h-clean" },
          h("table", { class: "cleanup" },
            h("thead", {}, h("tr", {}, [t("cleanup.object"), t("cleanup.reason"), t("cleanup.age"), t("cleanup.action")].map((c) => h("th", { scope: "col" }, c)))),
            h("tbody", {}, r.items.map(row)))),
    r.truncated ? h("p", { class: "muted" }, t("cleanup.truncated")) : null,
    h("p", { class: "muted small-text" },
      r.last_purge
        ? t("cleanup.sentinel").replace("{n}", String(r.window_days)).replace("{e}", String(r.last_purge.entities)).replace("{d}", String(r.last_purge.devices)).replace("{at}", ctx.date(r.last_purge.at))
        : t("cleanup.sentinel_pending").replace("{n}", String(r.window_days))),
  );
}
