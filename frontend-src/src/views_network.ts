// Network tab (spec 13): N0 inventory always, N1 probes only for approved targets.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { JobInfo, NetworkDevice, NetworkTarget, WsError } from "./types.ts";
import { type Host, renderHostsCard } from "./views_hosts.ts";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const STATUS_CLASS: Record<NetworkTarget["status"], string> = {
  unknown: "status", reachable: "ok", service_closed: "sev-warning", unreachable: "sev-high",
};

export async function renderNetwork(ctx: Ctx): Promise<HTMLElement> {
  const { t } = ctx;
  const [inv, targets, hosts] = await Promise.all([
    ctx.ws<{ level: string; probe_capability: { status: string; reason_code: string | null }; devices: NetworkDevice[] }>({ type: "ha_sentinel/network/inventory" }),
    ctx.ws<{ items: NetworkTarget[]; allowlist: string[]; interval_s: number; active: boolean }>({ type: "ha_sentinel/network/targets/list" }),
    ctx.ws<{ items: Host[] }>({ type: "ha_sentinel/network/hosts/list" }),
  ]);
  const deviceName = new Map(inv.devices.map((d) => [d.device_id, d.name ?? d.device_id]));
  const head = h(
    "section",
    { class: "card" },
    h("div", { class: "row spread" }, h("h2", {}, t("tab.network")), h("span", { class: `badge ${targets.active ? "ok" : "status"}` }, `${t("net.level")} ${inv.level}`)),
    h("p", { class: "muted" }, t(targets.active ? "net.n1_hint" : "net.n0_hint").replace("{s}", String(targets.interval_s))),
    targets.active ? h("p", { class: "muted" }, `${t("net.allowlist")}: `, h("code", {}, targets.allowlist.join(", "))) : null,
  );
  return h("div", { class: "stack" }, head, renderHostsCard(ctx, hosts.items), targetsCard(ctx, targets.items, targets.active, inv.devices, deviceName, hosts.items), inventoryCard(ctx, inv.devices));
}

function inventoryCard(ctx: Ctx, devices: NetworkDevice[]): HTMLElement {
  const { t } = ctx;
  return h(
    "section",
    { class: "card", "aria-labelledby": "h-inv" },
    h("h2", { id: "h-inv" }, t("net.inventory")),
    h("p", { class: "muted" }, t("net.inventory_hint")),
    devices.length === 0
      ? h("p", { class: "muted" }, t("net.no_devices"))
      : h("table", {}, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, t("net.device")), h("th", { scope: "col" }, t("net.identity")), h("th", { scope: "col" }, t("net.targets")))),
          h("tbody", {}, devices.map((d) => h("tr", {},
            h("td", {}, d.name ?? "—", d.manufacturer ? h("div", { class: "sub muted" }, d.manufacturer) : null),
            h("td", {}, d.mac_hint.length ? h("code", {}, d.mac_hint.join(", ")) : "—", d.has_configuration_url ? h("div", { class: "muted" }, t("net.has_url")) : null),
            h("td", {}, String(d.linked_targets.length)))))),
  );
}

function targetsCard(ctx: Ctx, items: NetworkTarget[], active: boolean, devices: NetworkDevice[], deviceName: Map<string, string>, hosts: Host[]): HTMLElement {
  const { t } = ctx;
  const status = h("div", { role: "status", "aria-live": "polite" });
  const scan = h("button", { class: "secondary", disabled: !active || items.length === 0 }, t("net.scan")) as HTMLButtonElement;
  scan.addEventListener("click", async () => {
    scan.disabled = true;
    try {
      const started = await ctx.ws<{ job_id: string }>({ type: "ha_sentinel/network/scan", request_key: uuid4() });
      for (let i = 0; i < 40; i++) {
        const job = await ctx.ws<JobInfo>({ type: "ha_sentinel/jobs/get", job_id: started.job_id });
        if (!["queued", "running"].includes(job.state)) {
          const r = job.result as { probed?: number; skipped?: number } | null;
          ctx.toast(t("net.scanned").replace("{p}", String(r?.probed ?? 0)).replace("{s}", String(r?.skipped ?? 0)));
          ctx.rerender();
          return;
        }
        await sleep(700);
      }
    } catch (err) {
      status.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(err as WsError)));
    }
    scan.disabled = false;
  });
  const rows = items.map((x) => h(
    "li",
    {},
    h(
      "div",
      { class: "item" },
      h("div", { class: "row spread" }, h("span", { class: "title" }, x.label), h("span", { class: `badge ${STATUS_CLASS[x.status]}` }, t(`net.status.${x.status}`))),
      h("div", { class: "sub" }, h("code", {}, `${x.kind}://${x.host}:${x.port}${x.kind === "tcp" ? "" : x.path}`), x.allow_loopback ? ` · ${t("net.loopback")}` : ""),
      h("div", { class: "sub" }, x.last_outcome ? t(`net.outcome.${x.last_outcome}`) : t("net.never"), x.linked_device_id ? ` · ${t("net.linked")} ${deviceName.get(x.linked_device_id) ?? x.linked_device_id}` : ""),
      h(
        "div",
        { class: "row" },
        h("button", { class: "secondary small", onclick: () => void mutate(ctx, { type: "ha_sentinel/network/targets/update", target_id: x.id, enabled: !x.enabled }) }, t(x.enabled ? "net.disable" : "net.enable")),
        hosts.length
          ? h("label", { class: "row small-text" }, t("net.host_link"), " ",
              h("select", { "aria-label": `${t("net.host_link")}: ${x.label}`, onchange: (e: Event) => { const v = (e.target as HTMLSelectElement).value; void mutate(ctx, { type: "ha_sentinel/network/targets/update", target_id: x.id, host_id: v || null }); } },
                h("option", { value: "", selected: !x.host_id }, t("net.host_none")),
                hosts.map((hs) => h("option", { value: hs.id, selected: x.host_id === hs.id }, hs.display_name))))
          : null,
        h("button", { class: "secondary small", onclick: () => { if (confirm(t("net.delete_confirm"))) void mutate(ctx, { type: "ha_sentinel/network/targets/delete", target_id: x.id }); } }, t("net.delete")),
      ),
    ),
  ));
  return h(
    "section",
    { class: "card", "aria-labelledby": "h-tg" },
    h("div", { class: "row spread" }, h("h2", { id: "h-tg" }, t("net.targets_title")), scan),
    !active ? h("div", { class: "banner", role: "note" }, t("net.enable_hint")) : null,
    items.length ? h("ul", { class: "list" }, rows) : h("p", { class: "muted" }, t("net.no_targets")),
    status,
    active ? addForm(ctx, devices) : null,
  );
}

async function mutate(ctx: Ctx, msg: Record<string, unknown>): Promise<void> {
  try {
    await ctx.ws({ ...msg, request_key: uuid4() });
  } catch (err) {
    ctx.toast(ctx.errorText(err as WsError));
  }
  ctx.rerender();
}

function addForm(ctx: Ctx, devices: NetworkDevice[]): HTMLElement {
  const { t, state } = ctx;
  const d = state.targetDraft;
  const input = (id: string, key: keyof typeof d, type = "text") =>
    h("input", { id, type, value: String(d[key]), oninput: (e: Event) => { (d as Record<string, unknown>)[key] = (e.target as HTMLInputElement).value; } });
  const err = h("div", { role: "alert" });
  return h(
    "form",
    {
      onsubmit: async (e: Event) => {
        e.preventDefault();
        try {
          await ctx.ws({
            type: "ha_sentinel/network/targets/add",
            label: d.label.trim() || d.host.trim(),
            host: d.host.trim(),
            port: Number(d.port),
            kind: d.kind,
            path: d.path || "/",
            allow_loopback: d.loopback,
            linked_device_id: d.device || null,
            request_key: uuid4(),
          });
          state.targetDraft = { label: "", host: "", port: "80", kind: "tcp", path: "/", loopback: false, device: "" };
          ctx.rerender();
        } catch (e2) {
          const we = e2 as WsError;
          err.replaceChildren(h("p", { class: "banner error" }, t(`net.reason.${we.details?.reason ?? "generic"}`)));
        }
      },
    },
    h("h3", {}, t("net.add")),
    h("p", { class: "muted" }, t("net.add_hint")),
    h("div", { class: "grid2" },
      h("div", {}, h("label", { for: "nt-host" }, t("net.host")), input("nt-host", "host")),
      h("div", {}, h("label", { for: "nt-port" }, t("net.port")), input("nt-port", "port", "number"))),
    h("div", { class: "grid2" },
      h("div", {}, h("label", { for: "nt-label" }, t("import.label")), input("nt-label", "label")),
      h("div", {}, h("label", { for: "nt-kind" }, t("net.kind")),
        h("select", { id: "nt-kind", onchange: (e: Event) => { d.kind = (e.target as HTMLSelectElement).value; } },
          ["tcp", "http", "https"].map((k) => h("option", { value: k, selected: d.kind === k }, k.toUpperCase()))))),
    h("label", { for: "nt-path" }, t("net.path")),
    input("nt-path", "path"),
    h("label", { for: "nt-dev" }, t("net.link_device")),
    h("select", { id: "nt-dev", onchange: (e: Event) => { d.device = (e.target as HTMLSelectElement).value; } },
      h("option", { value: "" }, t("net.no_link")),
      devices.map((dev) => h("option", { value: dev.device_id, selected: d.device === dev.device_id }, dev.name ?? dev.device_id))),
    h("label", { class: "row check" }, h("input", { type: "checkbox", checked: d.loopback, onchange: (e: Event) => { d.loopback = (e.target as HTMLInputElement).checked; } }), t("net.allow_loopback")),
    err,
    h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, t("net.approve"))),
  );
}
