// Home Assistant backup (spec 10.5): state of the backup entities, link to the settings and
// an explicit request. Sentinel snapshots are not a backup; an accepted request is not a
// finished backup.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { WsError } from "./types.ts";

interface BackupStatus {
  available: boolean;
  service: string | null;
  settings_path: string;
  entities: Record<string, { entity_id: string; state: string | null }>;
}

const FIELDS = ["manager_state", "last_successful", "last_attempted", "next_scheduled"];
const isDate = (v: string) => /^\d{4}-\d{2}-\d{2}T/.test(v);

export async function renderBackupCard(ctx: Ctx, settingsLink: (path: string, label: string) => HTMLElement): Promise<HTMLElement> {
  const { t } = ctx;
  const s = await ctx.ws<BackupStatus>({ type: "ha_sentinel/backup/status" });
  const out = h("div", { role: "status", "aria-live": "polite" });
  const value = (v: string | null) => (v === null || v === "unknown" || v === "unavailable" ? t("backup.none") : isDate(v) ? ctx.date(v, true) : v);
  const rows = FIELDS.filter((f) => s.entities[f]).map((f) => h("tr", {}, h("th", { scope: "row" }, t(`backup.${f}`)), h("td", {}, value(s.entities[f].state))));
  const create = s.service
    ? h("button", {
        class: "secondary", type: "button",
        onclick: async () => {
          if (!confirm(t("backup.confirm"))) return;
          try {
            await ctx.ws({ type: "ha_sentinel/backup/create", request_key: uuid4() });
            out.replaceChildren(h("p", { class: "banner" }, t("backup.accepted")));
          } catch (e) {
            out.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(e as WsError)));
          }
        },
      }, t("backup.create"))
    : null;
  return h(
    "section",
    { class: "card", "aria-labelledby": "h-bkp" },
    h("h2", { id: "h-bkp" }, t("backup.title")),
    h("p", { class: "muted" }, t("backup.hint")),
    !s.available
      ? h("p", { class: "muted" }, t("backup.unavailable"))
      : rows.length ? h("table", {}, h("tbody", {}, rows)) : h("p", { class: "muted" }, t("backup.no_entities")),
    h("div", { class: "actions" }, create, settingsLink(s.settings_path, t("backup.open"))),
    out,
  );
}
