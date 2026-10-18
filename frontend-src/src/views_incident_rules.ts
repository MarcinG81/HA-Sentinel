// Rules behind an incident, with the option to switch them off right there, for the affected
// objects or for their whole device (spec 27.1: overrides are per object, audited, versioned).

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { IncidentDetail, WsError } from "./types.ts";

export function renderIncidentRules(ctx: Ctx, inc: IncidentDetail, done: () => void): HTMLElement | null {
  const { t } = ctx;
  const rules = inc.rules ?? [];
  if (rules.length === 0) return null;
  const status = h("div", { role: "status", "aria-live": "polite" });

  const run = async (question: string, msg: Record<string, unknown>) => {
    if (!confirm(question)) return;
    try {
      await ctx.ws({ ...msg, enabled: false, request_key: uuid4() });
      ctx.toast(t("irules.done"));
      done();
    } catch (e) {
      status.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(e as WsError)));
    }
  };

  return h(
    "section",
    { class: "card", "aria-labelledby": "h-irules" },
    h("h2", { id: "h-irules" }, t("irules.title")),
    h("p", { class: "muted" }, t("irules.hint")),
    h("ul", { class: "list" }, rules.map((r) => {
      const n = r.subject_uids.length;
      const name = `${r.rule_id} · ${t(`rule.${r.rule_id}`)}`;
      return h("li", {}, h("div", { class: "item" },
        h("div", { class: "row spread" }, h("span", { class: "title" }, name),
          h("span", { class: "badge status" }, t("irules.objects").replace("{n}", String(n)))),
        h("div", { class: "sub" }, t(`irules.why.${r.rule_id}`)),
        r.per_entity_only ? h("div", { class: "sub" }, t("irules.per_entity")) : null,
        h("div", { class: "row" },
          h("button", {
            class: "secondary small", type: "button",
            onclick: () => void run(t("irules.confirm_objects").replace("{rule}", name).replace("{n}", String(n)),
              { type: "ha_sentinel/rules/set_many", rule_id: r.rule_id, subject_uids: r.subject_uids }),
          }, n === 1 ? t("irules.off_one") : t("irules.off_objects").replace("{n}", String(n))),
          r.device
            ? h("button", {
                class: "secondary small", type: "button",
                onclick: () => void run(t("irules.confirm_device").replace("{rule}", name).replace("{device}", r.device!.name ?? ""),
                  { type: "ha_sentinel/rules/set", rule_id: r.rule_id, subject_uid: r.device!.uid }),
              }, t("irules.off_device").replace("{device}", r.device.name ?? ""))
            : null),
      ));
    })),
    h("p", { class: "muted small-text" }, t("irules.undo")),
    status,
  );
}
