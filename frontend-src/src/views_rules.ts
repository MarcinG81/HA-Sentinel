// Per-object rule overrides and heartbeat contracts (spec 8.1, 27.1) in the Doctor tab.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { GraphNode, GraphSearch, WsError } from "./types.ts";

interface Override {
  id: string;
  rule_id: string;
  subject_uid: string;
  subject_label: string | null;
  subject_deleted: boolean;
  subject_persistent: boolean;
  subject_kind?: "entity" | "device";
  enabled: boolean;
  params: Record<string, number>;
}

interface RulesList {
  items: Override[];
  ranges: Record<string, Record<string, [number, number]>>;
}

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

export async function renderRulesCard(ctx: Ctx): Promise<HTMLElement> {
  const { t } = ctx;
  const { items, ranges } = await ctx.ws<RulesList>({ type: "ha_sentinel/rules/list" });
  let picked: GraphNode | null = null;
  const hits = h("ul", { class: "list" });
  const chosen = h("p", { class: "muted" }, t("rules.pick_hint"));
  const query = h("input", { id: "rul-q", type: "search", placeholder: t("map.placeholder") }) as HTMLInputElement;
  const rule = h("select", { id: "rul-rule" }, Object.keys(ranges).map((r) => h("option", { value: r, selected: r === "DOC005" }, `${r} · ${t(`rule.${r}`)}`))) as HTMLSelectElement;
  const enabled = h("input", { id: "rul-on", type: "checkbox", checked: true }) as HTMLInputElement;
  const paramBox = h("div", { class: "grid2" });
  const err = h("div", { role: "alert" });
  let inputs: Record<string, HTMLInputElement> = {};
  // DOC007 calendar: weekdays, local time and tolerance instead of raw numbers.
  const dayBoxes = DAYS.map((_, i) => h("input", { id: `rul-day-${i}`, type: "checkbox", checked: i < 5 }) as HTMLInputElement);
  const time = h("input", { id: "rul-time", type: "time", value: "07:00" }) as HTMLInputElement;
  const tolerance = h("input", { id: "rul-tol", type: "number", min: "1", max: "240", value: "15", inputmode: "numeric" }) as HTMLInputElement;

  const renderParams = () => {
    inputs = {};
    if (rule.value === "DOC007") {
      paramBox.replaceChildren(
        h("p", { class: "muted span2" }, t("rules.doc007_hint")),
        h("fieldset", { class: "plain span2" }, h("legend", {}, t("rules.days")),
          h("div", { class: "row" }, DAYS.map((d, i) => h("label", { class: "row", for: `rul-day-${i}` }, dayBoxes[i], t(`day.${d}`))))),
        h("div", {}, h("label", { for: "rul-time" }, t("rules.time")), time),
        h("div", {}, h("label", { for: "rul-tol" }, t("rules.param.tolerance_min")), tolerance),
      );
      return;
    }
    paramBox.replaceChildren(...Object.entries(ranges[rule.value] ?? {}).map(([name, [lo, hi]]) => {
      const id = `rul-p-${name}`;
      const input = h("input", { id, type: "number", min: String(lo), max: String(hi), step: "1", inputmode: "numeric" }) as HTMLInputElement;
      inputs[name] = input;
      return h("div", {}, h("label", { for: id }, `${t(`rules.param.${name}`)} (${lo}–${hi})`), input);
    }));
    const contract = rule.value === "DOC005";
    paramBox.prepend(h("p", { class: "muted span2" }, t(contract ? "rules.doc005_hint" : "rules.params_hint")));
  };
  rule.addEventListener("change", renderParams);
  renderParams();

  const search = async () => {
    if (!query.value.trim()) return;
    const res = await ctx.ws<GraphSearch>({ type: "ha_sentinel/graph/search", q: query.value.trim() });
    // A device covers all its entities (also later ones); contracts stay per entity.
    const perEntity = ["DOC005", "DOC007"].includes(rule.value);
    const ok = res.items.filter((n) => n.type === "entity" || (n.type === "device" && !perEntity));
    hits.replaceChildren(...ok.map((n) => h("li", {}, h("button", {
      class: "item", type: "button",
      onclick: () => { picked = n; chosen.replaceChildren(h("strong", {}, n.label), n.type === "device" ? ` · ${t("node.device")}` : ""); hits.replaceChildren(); },
    }, h("div", { class: "row spread" }, h("span", { class: "title" }, n.label), n.type === "device" ? h("span", { class: "badge status" }, t("node.device")) : null)))));
    if (!ok.length) hits.replaceChildren(h("li", { class: "muted" }, t("map.no_hits")));
  };

  const form = h(
    "form",
    {
      onsubmit: async (e: Event) => {
        e.preventDefault();
        if (!picked) { query.focus(); return; }
        const params: Record<string, number> = {};
        for (const [name, input] of Object.entries(inputs)) {
          if (input.value.trim() !== "") params[name] = Number(input.value);
        }
        if (rule.value === "DOC007") {
          const [hh, mm] = (time.value || "00:00").split(":").map(Number);
          params.days_mask = dayBoxes.reduce((mask, box, i) => (box.checked ? mask | (1 << i) : mask), 0);
          params.minute = hh * 60 + mm;
          params.tolerance_min = Number(tolerance.value);
        }
        try {
          await ctx.ws({ type: "ha_sentinel/rules/set", rule_id: rule.value, subject_uid: picked.uid, enabled: enabled.checked, params, request_key: uuid4() });
          ctx.rerender();
        } catch (e2) {
          err.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(e2 as WsError)));
        }
      },
    },
    h("h3", {}, t("rules.new")),
    h("label", { for: "rul-q" }, t("rules.object")),
    h("div", { class: "row" }, h("div", { class: "grow" }, query), h("button", { class: "secondary", type: "button", onclick: () => void search() }, t("map.search"))),
    hits,
    chosen,
    h("div", { class: "grid2" },
      h("div", {}, h("label", { for: "rul-rule" }, t("rules.rule")), rule),
      h("div", { class: "row" }, enabled, h("label", { for: "rul-on" }, t("rules.enabled")))),
    paramBox,
    err,
    h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, t("rules.save"))),
  );

  const describe = (o: Override) => {
    if (o.rule_id === "DOC007" && o.params.days_mask !== undefined) {
      const days = DAYS.filter((_, i) => (o.params.days_mask >> i) & 1).map((d) => t(`day.${d}`)).join(", ");
      const hhmm = `${String(Math.floor(o.params.minute / 60)).padStart(2, "0")}:${String(o.params.minute % 60).padStart(2, "0")}`;
      return `${o.enabled ? "" : `${t("rules.disabled")} · `}${days} · ${hhmm} ± ${o.params.tolerance_min} min`;
    }
    const parts = Object.entries(o.params).map(([k, v]) => `${t(`rules.param.${k}`)}: ${v}`);
    if (!o.enabled) parts.unshift(t("rules.disabled"));
    return parts.join(" · ") || t("rules.defaults");
  };

  return h(
    "section",
    { class: "card", "aria-labelledby": "h-rul" },
    h("h2", { id: "h-rul" }, t("rules.title")),
    h("p", { class: "muted" }, t("rules.hint")),
    items.length === 0
      ? h("p", { class: "muted" }, t("rules.none"))
      : h("ul", { class: "list" }, items.map((o) => h("li", {}, h("div", { class: "item" },
          h("div", { class: "row spread" },
            h("span", { class: "title" }, o.subject_label ?? t("rules.subject_gone"), o.subject_kind === "device" ? ` · ${t("node.device")}` : ""),
            h("span", { class: `badge ${o.enabled ? "ok" : "status"}` }, `${o.rule_id} · ${t(`rule.${o.rule_id}`)}`)),
          h("div", { class: "sub" }, describe(o) + (o.subject_deleted ? ` · ${t("rules.subject_gone")}` : !o.subject_persistent ? ` · ${t("rules.not_persistent")}` : "")),
          h("button", { class: "secondary small", onclick: async () => { await ctx.ws({ type: "ha_sentinel/rules/delete", override_id: o.id, request_key: uuid4() }); ctx.rerender(); } }, t("rules.remove")))))),
    form,
  );
}
