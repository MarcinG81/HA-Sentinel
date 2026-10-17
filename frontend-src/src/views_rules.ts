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
  enabled: boolean;
  params: Record<string, number>;
}

interface RulesList {
  items: Override[];
  ranges: Record<string, Record<string, [number, number]>>;
}

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

  const renderParams = () => {
    inputs = {};
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
    const ok = res.items.filter((n) => n.type === "entity");
    hits.replaceChildren(...ok.map((n) => h("li", {}, h("button", {
      class: "item", type: "button",
      onclick: () => { picked = n; chosen.replaceChildren(h("strong", {}, n.label)); hits.replaceChildren(); },
    }, h("span", { class: "title" }, n.label)))));
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
            h("span", { class: "title" }, o.subject_label ?? t("rules.subject_gone")),
            h("span", { class: `badge ${o.enabled ? "ok" : "status"}` }, `${o.rule_id} · ${t(`rule.${o.rule_id}`)}`)),
          h("div", { class: "sub" }, describe(o) + (o.subject_deleted ? ` · ${t("rules.subject_gone")}` : !o.subject_persistent ? ` · ${t("rules.not_persistent")}` : "")),
          h("button", { class: "secondary small", onclick: async () => { await ctx.ws({ type: "ha_sentinel/rules/delete", override_id: o.id, request_key: uuid4() }); ctx.rerender(); } }, t("rules.remove")))))),
    form,
  );
}
