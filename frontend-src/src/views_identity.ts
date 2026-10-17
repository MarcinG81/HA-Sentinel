// Suggested identity continuity (spec 6.1): a deleted and re-added entity is never merged
// automatically; the admin confirms (settings move to the new identity) or rejects.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { WsError } from "./types.ts";

interface Side {
  uid: string;
  entity_id: string | null;
  deleted: boolean;
}

interface Suggestion {
  id: string;
  old: Side;
  new: Side;
  reason: string;
  created: string;
}

export async function renderIdentityCard(ctx: Ctx): Promise<HTMLElement | null> {
  const { t } = ctx;
  const { items } = await ctx.ws<{ items: Suggestion[] }>({ type: "ha_sentinel/identity/list" });
  if (items.length === 0) return null;
  const err = h("div", { role: "alert" });
  const decide = async (id: string, confirm: boolean) => {
    try {
      await ctx.ws({ type: "ha_sentinel/identity/decide", suggestion_id: id, confirm, request_key: uuid4() });
      ctx.rerender();
    } catch (e) {
      err.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(e as WsError)));
    }
  };
  return h(
    "section",
    { class: "card", "aria-labelledby": "h-idn" },
    h("h2", { id: "h-idn" }, t("identity.title")),
    h("p", { class: "muted" }, t("identity.hint")),
    h("ul", { class: "list" }, items.map((s) => h("li", {}, h("div", { class: "item" },
      h("div", { class: "title" }, s.new.entity_id ?? s.new.uid),
      h("div", { class: "sub" }, t("identity.line").replace("{date}", ctx.date(s.created))),
      h("div", { class: "row" },
        h("button", { class: "primary small", type: "button", onclick: () => void decide(s.id, true) }, t("identity.confirm")),
        h("button", { class: "secondary small", type: "button", onclick: () => void decide(s.id, false) }, t("identity.reject"))))))),
    err,
  );
}
