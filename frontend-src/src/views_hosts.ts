// N0 network hosts (spec 13.1, 13.2): manual hosts, suggestions and admin-confirmed links.
// Reachability (probes) and the state of the linked devices' entities are shown separately.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { WsError } from "./types.ts";

export interface Host {
  id: string;
  display_name: string;
  keys: { mac?: string; hostname?: string };
  addresses: { family: string; value: string }[];
  links: { device_id: string; name: string | null; missing: boolean }[];
  suggestions: { device_id: string; name: string | null; reasons: string[]; confidence: number }[];
  reachability: string;
  ha_entities: { available: number; unavailable: number; unknown: number };
  endpoints: string[];
}

export function renderHostsCard(ctx: Ctx, items: Host[]): HTMLElement {
  const { t } = ctx;
  const err = h("div", { role: "alert" });
  const fail = (e: unknown) => err.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(e as WsError)));
  const act = async (msg: Record<string, unknown>) => {
    try {
      await ctx.ws({ ...msg, request_key: uuid4() });
      ctx.rerender();
    } catch (e) {
      fail(e);
    }
  };

  const input = (id: string, placeholder = "") => h("input", { id, type: "text", maxlength: "253", placeholder }) as HTMLInputElement;
  const name = input("hst-name");
  const mac = input("hst-mac", "aa:bb:cc:dd:ee:ff");
  const hostname = input("hst-hostname", "nas.local");
  const addresses = input("hst-addr", "192.168.1.10");
  const form = h(
    "form",
    {
      onsubmit: async (e: Event) => {
        e.preventDefault();
        if (!name.value.trim()) { name.focus(); return; }
        await act({
          type: "ha_sentinel/network/hosts/save", display_name: name.value.trim(),
          mac: mac.value.trim() || null, hostname: hostname.value.trim() || null,
          addresses: addresses.value.split(",").map((s) => s.trim()).filter(Boolean),
        });
      },
    },
    h("h3", {}, t("hosts.new")),
    h("div", { class: "grid2" },
      h("div", {}, h("label", { for: "hst-name" }, t("hosts.name")), name),
      h("div", {}, h("label", { for: "hst-mac" }, t("hosts.mac")), mac)),
    h("div", { class: "grid2" },
      h("div", {}, h("label", { for: "hst-hostname" }, t("hosts.hostname")), hostname),
      h("div", {}, h("label", { for: "hst-addr" }, t("hosts.addresses")), addresses)),
    h("p", { class: "muted small-text" }, t("hosts.identity_hint")),
    h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, t("hosts.add"))),
  );

  const counts = (c: Host["ha_entities"]) => {
    const total = c.available + c.unavailable + c.unknown;
    if (total === 0) return t("hosts.ha_none");
    return t("hosts.ha_counts").replace("{ok}", String(c.available)).replace("{bad}", String(c.unavailable)).replace("{unk}", String(c.unknown));
  };

  const hostItem = (x: Host) => h(
    "li",
    {},
    h(
      "div",
      { class: "item" },
      h("div", { class: "row spread" },
        h("span", { class: "title" }, x.display_name),
        h("span", { class: `badge ${x.reachability === "reachable" ? "ok" : x.reachability === "unreachable" ? "sev-high" : "status"}` }, `${t("hosts.reach")}: ${t(`hosts.reach.${x.reachability}`)}`)),
      h("div", { class: "sub" }, [x.keys.mac, x.keys.hostname, ...x.addresses.map((a) => a.value)].filter(Boolean).join(" · ") || t("hosts.no_identity")),
      h("div", { class: "sub" }, `${t("hosts.ha_state")}: ${counts(x.ha_entities)}`),
      x.links.length
        ? h("ul", { class: "plain" }, x.links.map((l) => h("li", { class: "row" },
            h("span", {}, `${t("hosts.linked")}: ${l.name ?? l.device_id}${l.missing ? ` (${t("hosts.device_gone")})` : ""}`),
            h("button", { class: "secondary small", type: "button", onclick: () => void act({ type: "ha_sentinel/network/hosts/link", host_id: x.id, device_id: l.device_id, unlink: true }) }, t("hosts.unlink")))))
        : null,
      x.suggestions.length
        ? h("div", {}, h("p", { class: "sub" }, t("hosts.suggestions")),
            h("ul", { class: "plain" }, x.suggestions.map((s) => h("li", { class: "row" },
              h("span", {}, `${s.name ?? s.device_id} · ${s.reasons.map((r) => t(`hosts.reason.${r}`)).join(", ")}`),
              h("button", { class: "secondary small", type: "button", onclick: () => void act({ type: "ha_sentinel/network/hosts/link", host_id: x.id, device_id: s.device_id }) }, t("hosts.link"))))))
        : null,
      h("button", { class: "secondary small", type: "button", onclick: () => void act({ type: "ha_sentinel/network/hosts/delete", host_id: x.id }) }, t("rules.remove")),
    ),
  );

  return h(
    "section",
    { class: "card", "aria-labelledby": "h-hosts" },
    h("h2", { id: "h-hosts" }, t("hosts.title")),
    h("p", { class: "muted" }, t("hosts.hint")),
    items.length === 0 ? h("p", { class: "muted" }, t("hosts.none")) : h("ul", { class: "list" }, items.map(hostItem)),
    err,
    form,
  );
}
