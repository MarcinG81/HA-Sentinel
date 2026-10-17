// Explicit numeric sources (spec 5.1, 8.1): manual battery mapping and signal scales.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { GraphNode, GraphSearch, WsError } from "./types.ts";

interface Source {
  id: string;
  subject_uid: string;
  subject_label: string | null;
  subject_deleted: boolean;
  subject_persistent: boolean;
  purpose: "battery" | "signal";
  scale_min: number;
  scale_max: number;
  threshold: number | null;
  sampling: boolean;
}

interface SourceList {
  items: Source[];
  numeric_sampling: boolean;
}

// Typical scales as a starting point; the admin confirms the values for the device.
const PRESETS: Record<string, [number, number, number | null]> = {
  battery: [0, 100, null],
  battery255: [0, 255, null],
  lqi: [0, 255, 60],
  rssi: [-100, -30, -80],
};

export async function renderNumericCard(ctx: Ctx): Promise<HTMLElement> {
  const { t } = ctx;
  const { items, numeric_sampling } = await ctx.ws<SourceList>({ type: "ha_sentinel/numeric/list" });
  let picked: GraphNode | null = null;
  const hits = h("ul", { class: "list" });
  const chosen = h("p", { class: "muted" }, t("rules.pick_hint"));
  const query = h("input", { id: "num-q", type: "search", placeholder: t("map.placeholder") }) as HTMLInputElement;
  const num = (id: string) => h("input", { id, type: "number", step: "any", inputmode: "decimal" }) as HTMLInputElement;
  const min = num("num-min");
  const max = num("num-max");
  const threshold = num("num-thr");
  const thresholdBox = h("div", {}, h("label", { for: "num-thr" }, t("num.threshold")), threshold);
  const preset = h("select", { id: "num-preset" }, Object.keys(PRESETS)
    .filter((k) => numeric_sampling || !["lqi", "rssi"].includes(k))
    .map((k) => h("option", { value: k }, t(`num.preset.${k}`)))) as HTMLSelectElement;
  const err = h("div", { role: "alert" });

  const applyPreset = () => {
    const [lo, hi, thr] = PRESETS[preset.value];
    min.value = String(lo);
    max.value = String(hi);
    threshold.value = thr === null ? "" : String(thr);
    thresholdBox.hidden = thr === null;
  };
  preset.addEventListener("change", applyPreset);
  applyPreset();

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
        const signal = !thresholdBox.hidden;
        try {
          await ctx.ws({
            type: "ha_sentinel/numeric/set", subject_uid: picked.uid, purpose: signal ? "signal" : "battery",
            scale_min: Number(min.value), scale_max: Number(max.value),
            ...(signal ? { threshold: Number(threshold.value) } : {}), request_key: uuid4(),
          });
          ctx.rerender();
        } catch (e2) {
          err.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(e2 as WsError)));
        }
      },
    },
    h("h3", {}, t("num.new")),
    h("label", { for: "num-q" }, t("rules.object")),
    h("div", { class: "row" }, h("div", { class: "grow" }, query), h("button", { class: "secondary", type: "button", onclick: () => void search() }, t("map.search"))),
    hits,
    chosen,
    h("label", { for: "num-preset" }, t("num.kind")),
    preset,
    h("div", { class: "grid2" },
      h("div", {}, h("label", { for: "num-min" }, t("num.scale_min")), min),
      h("div", {}, h("label", { for: "num-max" }, t("num.scale_max")), max)),
    thresholdBox,
    err,
    h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, t("rules.save"))),
  );

  const describe = (s: Source) => {
    const parts = [`${t("num.scale")}: ${s.scale_min} … ${s.scale_max}`];
    if (s.threshold !== null) parts.push(`${t("num.threshold")}: ${s.threshold}`);
    if (!s.sampling) parts.push(t("num.sampling_off"));
    if (s.subject_deleted) parts.push(t("rules.subject_gone"));
    else if (!s.subject_persistent) parts.push(t("rules.not_persistent"));
    return parts.join(" · ");
  };

  return h(
    "section",
    { class: "card", "aria-labelledby": "h-num" },
    h("h2", { id: "h-num" }, t("num.title")),
    h("p", { class: "muted" }, t("num.hint")),
    numeric_sampling ? null : h("p", { class: "muted" }, t("num.signal_needs_option")),
    items.length === 0
      ? h("p", { class: "muted" }, t("num.none"))
      : h("ul", { class: "list" }, items.map((s) => h("li", {}, h("div", { class: "item" },
          h("div", { class: "row spread" },
            h("span", { class: "title" }, s.subject_label ?? t("rules.subject_gone")),
            h("span", { class: `badge ${s.sampling ? "ok" : "status"}` }, t(`num.purpose.${s.purpose}`))),
          h("div", { class: "sub" }, describe(s)),
          h("button", { class: "secondary small", onclick: async () => { await ctx.ws({ type: "ha_sentinel/numeric/delete", source_id: s.id, request_key: uuid4() }); ctx.rerender(); } }, t("rules.remove")))))),
    form,
  );
}
