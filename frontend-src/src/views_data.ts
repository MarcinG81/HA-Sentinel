// Export (Diagnostics tab) and Sentinel data management (Settings tab), spec 15.4 and 17.1.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { DataStatus, JobInfo, WsError } from "./types.ts";

const SCOPES = ["diagnostics", "incidents", "config_history"];
const DELETE_SCOPES = ["observations", "incidents", "config_history", "all"];
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function renderExportCard(ctx: Ctx): HTMLElement {
  const { t, state } = ctx;
  const status = h("div", { role: "status", "aria-live": "polite" });
  const preview = () =>
    h(
      "ul",
      { class: "muted" },
      state.exportScopes.map((s) => h("li", {}, t(`export.contains.${s}${state.exportRedacted ? "" : ".raw"}`))),
    );
  const previewBox = h("div", {}, preview());
  const refreshPreview = () => previewBox.replaceChildren(preview());
  const button = h("button", { class: "primary", type: "submit" }, t("export.create")) as HTMLButtonElement;

  const form = h(
    "form",
    {
      onsubmit: async (e: Event) => {
        e.preventDefault();
        if (state.exportScopes.length === 0) return;
        button.disabled = true;
        try {
          const started = await ctx.ws<{ job_id: string }>({
            type: "ha_sentinel/export/create",
            scopes: state.exportScopes,
            redacted: state.exportRedacted,
            request_key: uuid4(),
          });
          state.exportJob = started.job_id;
          await followJob(ctx, started.job_id, status);
        } catch (err) {
          status.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(err as WsError)));
        }
        button.disabled = false;
      },
    },
    h(
      "fieldset",
      { class: "plain" },
      h("legend", {}, t("export.scopes")),
      SCOPES.map((s) =>
        h(
          "label",
          { class: "row check" },
          h("input", {
            type: "checkbox",
            checked: state.exportScopes.includes(s),
            onchange: (e: Event) => {
              const on = (e.target as HTMLInputElement).checked;
              state.exportScopes = on ? [...new Set([...state.exportScopes, s])] : state.exportScopes.filter((x) => x !== s);
              refreshPreview();
            },
          }),
          t(`export.scope.${s}`),
        ),
      ),
    ),
    h(
      "label",
      { class: "row check" },
      h("input", { type: "checkbox", checked: state.exportRedacted, onchange: (e: Event) => { state.exportRedacted = (e.target as HTMLInputElement).checked; refreshPreview(); } }),
      t("export.redacted"),
    ),
    h("p", { class: "muted" }, t("export.redacted_hint")),
    h("h3", {}, t("export.preview")),
    previewBox,
    h("div", { class: "actions" }, button),
    status,
  );
  return h("section", { class: "card", "aria-labelledby": "h-exp" }, h("h2", { id: "h-exp" }, t("export.title")), form);
}

async function followJob(ctx: Ctx, jobId: string, status: HTMLElement): Promise<void> {
  const { t } = ctx;
  for (let i = 0; i < 120; i++) {
    const job = await ctx.ws<JobInfo>({ type: "ha_sentinel/jobs/get", job_id: jobId });
    const pct = Math.round((job.progress ?? 0) * 100);
    if (job.state === "succeeded" && job.result?.export_id) {
      const exportId = job.result.export_id;
      const size = `${((job.result.bytes ?? 0) / 1024).toFixed(1)} KiB`;
      status.replaceChildren(
        h("p", {}, `${t("export.ready")} (${size}) · ${t("export.expires")} ${ctx.date(job.result.expires_at ?? null)}`),
        h("button", { class: "secondary", onclick: () => void download(ctx, exportId) }, t("export.download")),
      );
      return;
    }
    if (["failed", "cancelled", "interrupted"].includes(job.state)) {
      status.replaceChildren(h("p", { class: "banner error" }, `${t(`job.${job.state}`)}${job.error_code ? ` (${job.error_code})` : ""}`));
      return;
    }
    status.replaceChildren(
      h("p", {}, `${t(`job.${job.state}`)} · ${pct}%`),
      h("button", { class: "link", onclick: () => void ctx.ws({ type: "ha_sentinel/jobs/cancel", job_id: jobId, request_key: uuid4() }) }, t("action.cancel")),
    );
    await sleep(1000);
  }
}

async function download(ctx: Ctx, exportId: string): Promise<void> {
  const resp = await ctx.fetch(`/api/ha_sentinel/exports/${exportId}`);
  if (!resp.ok) {
    ctx.toast(ctx.t("export.download_failed"));
    return;
  }
  const url = URL.createObjectURL(await resp.blob());
  const a = h("a", { href: url, download: `ha-sentinel-export-${exportId.slice(0, 8)}.zip` }) as HTMLAnchorElement;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function renderDataCard(ctx: Ctx): Promise<HTMLElement> {
  const { t, state } = ctx;
  const st = await ctx.ws<DataStatus>({ type: "ha_sentinel/data/status" });
  const mib = (st.storage.db_bytes / (1024 * 1024)).toFixed(1);
  const confirm = h("input", { id: "del-confirm", type: "text", autocomplete: "off", spellcheck: "false" }) as HTMLInputElement;
  const scope = h(
    "select",
    { id: "del-scope", onchange: (e: Event) => { state.deleteScope = (e.target as HTMLSelectElement).value; hint.textContent = t("data.type_to_confirm").replace("{scope}", state.deleteScope); } },
    DELETE_SCOPES.map((s) => h("option", { value: s, selected: s === state.deleteScope }, t(`data.scope.${s}`))),
  ) as HTMLSelectElement;
  const hint = h("p", { class: "muted", id: "del-hint" }, t("data.type_to_confirm").replace("{scope}", state.deleteScope));
  confirm.setAttribute("aria-describedby", "del-hint");
  const submit = h("button", { class: "primary danger", type: "submit" }, t("data.delete")) as HTMLButtonElement;
  const form = h(
    "form",
    {
      onsubmit: async (e: Event) => {
        e.preventDefault();
        if (confirm.value !== state.deleteScope) { confirm.focus(); return; }
        submit.disabled = true;
        try {
          await ctx.ws({ type: "ha_sentinel/data/delete", scope: state.deleteScope, confirm: confirm.value, request_key: uuid4() });
          ctx.toast(t(state.deleteScope === "all" ? "data.deleted_all" : "data.deleted"));
        } catch (err) {
          ctx.toast(ctx.errorText(err as WsError));
        }
        ctx.rerender();
      },
    },
    h("label", { for: "del-scope" }, t("data.scope")),
    scope,
    h("label", { for: "del-confirm" }, t("data.confirm")),
    confirm,
    hint,
    h("div", { class: "actions" }, submit),
  );
  const retention = Object.entries(st.retention).map(([k, v]) => h("tr", {}, h("th", { scope: "row" }, t(`opt.${k}`)), h("td", {}, String(v))));
  return h(
    "section",
    { class: "card", "aria-labelledby": "h-data" },
    h("h2", { id: "h-data" }, t("data.title")),
    h(
      "table",
      {},
      h("tbody", {},
        h("tr", {}, h("th", { scope: "row" }, t("data.db_size")), h("td", {}, `${mib} MiB`)),
        h("tr", {}, h("th", { scope: "row" }, t("data.observations")), h("td", {}, String(st.storage.observations))),
        retention,
        h("tr", {}, h("th", { scope: "row" }, t("data.last_purge")), h("td", {}, st.last_purge ? ctx.date(st.last_purge.at, true) : t("data.never"))),
      ),
    ),
    h("h3", {}, t("data.delete_title")),
    h("div", { class: "banner", role: "note" }, t("data.scope_note")),
    form,
  );
}
