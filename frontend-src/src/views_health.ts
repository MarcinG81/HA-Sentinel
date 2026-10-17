// Health score components, exclusions and unit weights (spec 8.3).

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { WsError } from "./types.ts";

interface Unit {
  unit: string;
  kind: "device" | "entity";
  label: string;
  entities: number;
  rules: string[];
  weight?: number;
  penalty?: string;
  covered?: boolean;
  excluded?: string;
}

let showAll = false; // per page view; not worth persisting

export async function renderHealthComponents(ctx: Ctx): Promise<HTMLElement> {
  const { t } = ctx;
  const data = await ctx.ws<{ units: Unit[] }>({ type: "ha_sentinel/health/components" });
  const err = h("div", { role: "alert" });
  const notable = (u: Unit) => u.excluded !== undefined || u.covered === false || (u.weight ?? 1) !== 1 || Number(u.penalty ?? 0) > 0;
  const units = [...data.units].sort((a, b) => Number(b.penalty ?? 0) - Number(a.penalty ?? 0) || a.label.localeCompare(b.label));
  const shown = showAll ? units : units.filter(notable);

  const weightSelect = (u: Unit) => h(
    "select",
    {
      "aria-label": `${t("health.weight")}: ${u.label}`,
      onchange: async (e: Event) => {
        try {
          await ctx.ws({ type: "ha_sentinel/health/weight", unit_uid: u.unit, weight: Number((e.target as HTMLSelectElement).value), request_key: uuid4() });
          ctx.rerender();
        } catch (e2) {
          err.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(e2 as WsError)));
        }
      },
    },
    [1, 2, 3, 4, 5].map((w) => h("option", { value: String(w), selected: (u.weight ?? 1) === w }, String(w))),
  );

  const row = (u: Unit) => h(
    "tr",
    {},
    h("th", { scope: "row" }, u.label, h("div", { class: "muted small-text" }, `${t(`health.kind.${u.kind}`)} · ${t("health.entities").replace("{n}", String(u.entities))}`)),
    h("td", {}, u.excluded ? t(`health.excluded.${u.excluded}`) : u.rules.length ? u.rules.join(", ") : "—"),
    h("td", {}, u.excluded ? "—" : String(Number(u.penalty ?? 0))),
    h("td", {}, u.excluded ? "—" : t(u.covered ? "health.covered" : "health.not_covered")),
    h("td", {}, u.excluded ? "—" : weightSelect(u)),
  );

  return h(
    "section",
    { class: "card", "aria-labelledby": "h-hc" },
    h("div", { class: "row spread" },
      h("h2", { id: "h-hc" }, t("health.components")),
      h("label", { class: "row" }, h("input", { type: "checkbox", checked: showAll, onchange: (e: Event) => { showAll = (e.target as HTMLInputElement).checked; ctx.rerender(); } }), t("health.show_all"))),
    h("p", { class: "muted" }, t("health.components_hint")),
    shown.length === 0
      ? h("p", { class: "muted" }, t("health.nothing_notable").replace("{n}", String(units.length)))
      : h("div", { class: "scroll", tabindex: "0", role: "region", "aria-labelledby": "h-hc" },
          h("table", { class: "metrics" },
            h("thead", {}, h("tr", {}, [t("health.unit"), t("health.rules"), t("health.penalty"), t("health.coverage"), t("health.weight")].map((c) => h("th", { scope: "col" }, c)))),
            h("tbody", {}, shown.map(row)))),
    err,
  );
}
