// Maintenance windows (spec 8.2) shown in the Doctor tab.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { GraphNode, GraphSearch, WsError } from "./types.ts";

interface Window {
  id: string;
  scope_kind: string;
  scope_id: string;
  label: string;
  reason: string;
  start: string;
  end: string;
  active: boolean;
}

const DURATIONS = [30, 60, 240, 1440];

export async function renderMaintenanceCard(ctx: Ctx): Promise<HTMLElement> {
  const { t } = ctx;
  const { items } = await ctx.ws<{ items: Window[] }>({ type: "ha_sentinel/maintenance/list" });
  let picked: GraphNode | null = null;
  const hits = h("ul", { class: "list" });
  const chosen = h("p", { class: "muted" }, t("mnt.pick_hint"));
  const query = h("input", { id: "mnt-q", type: "search", placeholder: t("map.placeholder") }) as HTMLInputElement;
  const reason = h("input", { id: "mnt-reason", type: "text", maxlength: "200" }) as HTMLInputElement;
  const duration = h("select", { id: "mnt-dur" }, DURATIONS.map((m) => h("option", { value: String(m) }, t(`mnt.d${m}`)))) as HTMLSelectElement;
  const err = h("div", { role: "alert" });

  const search = async () => {
    if (!query.value.trim()) return;
    const res = await ctx.ws<GraphSearch>({ type: "ha_sentinel/graph/search", q: query.value.trim() });
    const ok = res.items.filter((n) => ["entity", "device", "integration_entry"].includes(n.type));
    hits.replaceChildren(...ok.map((n) => h("li", {}, h("button", {
      class: "item", type: "button",
      onclick: () => { picked = n; chosen.replaceChildren(h("strong", {}, n.label), ` · ${t(`node.${n.type}`)}`); hits.replaceChildren(); },
    }, h("div", { class: "row spread" }, h("span", { class: "title" }, n.label), h("span", { class: "badge status" }, t(`node.${n.type}`)))))));
    if (!ok.length) hits.replaceChildren(h("li", { class: "muted" }, t("map.no_hits")));
  };

  const form = h(
    "form",
    {
      onsubmit: async (e: Event) => {
        e.preventDefault();
        if (!picked) { query.focus(); return; }
        if (!reason.value.trim()) { reason.focus(); return; }
        try {
          await ctx.ws({ type: "ha_sentinel/maintenance/create", scope_uid: picked.uid, minutes: Number(duration.value), reason: reason.value.trim(), request_key: uuid4() });
          ctx.rerender();
        } catch (e2) {
          err.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(e2 as WsError)));
        }
      },
    },
    h("h3", {}, t("mnt.new")),
    h("label", { for: "mnt-q" }, t("mnt.object")),
    h("div", { class: "row" }, h("div", { class: "grow" }, query), h("button", { class: "secondary", type: "button", onclick: () => void search() }, t("map.search"))),
    hits,
    chosen,
    h("div", { class: "grid2" },
      h("div", {}, h("label", { for: "mnt-reason" }, t("suppress.reason")), reason),
      h("div", {}, h("label", { for: "mnt-dur" }, t("suppress.duration")), duration)),
    err,
    h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, t("mnt.start"))),
  );

  return h(
    "section",
    { class: "card", "aria-labelledby": "h-mnt" },
    h("h2", { id: "h-mnt" }, t("mnt.title")),
    h("p", { class: "muted" }, t("mnt.hint")),
    items.length === 0
      ? h("p", { class: "muted" }, t("mnt.none"))
      : h("ul", { class: "list" }, items.map((w) => h("li", {}, h("div", { class: "item" },
          h("div", { class: "row spread" }, h("span", { class: "title" }, w.label), h("span", { class: `badge ${w.active ? "ok" : "status"}` }, t(w.active ? "mnt.active" : "mnt.planned"))),
          h("div", { class: "sub" }, `${t(`mnt.kind.${w.scope_kind}`)} · ${ctx.date(w.start)} → ${ctx.date(w.end)} · ${w.reason}`),
          h("button", { class: "secondary small", onclick: async () => { await ctx.ws({ type: "ha_sentinel/maintenance/cancel", window_id: w.id, request_key: uuid4() }); ctx.rerender(); } }, t("mnt.end")))))),
    form,
  );
}
