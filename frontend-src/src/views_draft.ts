// Autopilot draft wizard (spec 11.1). The result is YAML to export or import as a snapshot;
// nothing here installs an automation or calls an HA service.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import { contentHash } from "./sha256.ts";
import type { LintFinding, WsError } from "./types.ts";
import { codeBlock, download, lintMessage } from "./views_e3.ts";

interface DraftResult {
  yaml: string;
  completeness: number;
  findings: LintFinding[];
  valid: boolean;
  inventory_complete: boolean;
}

const field = (id: string, label: string, input: HTMLElement, hint?: string) =>
  h("div", {}, h("label", { for: id }, label), input, hint ? h("p", { class: "muted small-text" }, hint) : null);

export function renderDraftCard(ctx: Ctx): HTMLElement {
  const { t } = ctx;
  const text = (id: string, placeholder = "") => h("input", { id, type: "text", maxlength: "100", placeholder }) as HTMLInputElement;
  const number = (id: string, min: string, max: string) => h("input", { id, type: "number", min, max, step: "any", inputmode: "decimal" }) as HTMLInputElement;
  const select = (id: string, options: [string, string][]) => h("select", { id }, options.map(([v, l]) => h("option", { value: v }, l))) as HTMLSelectElement;

  const alias = text("dr-alias");
  const kind = select("dr-kind", [["state", t("draft.trigger.state")], ["numeric_state", t("draft.trigger.numeric")]]);
  const trigEntity = text("dr-tent", "binary_sensor.front_door");
  const to = text("dr-to", "on");
  const above = number("dr-above", "-1000000", "1000000");
  const below = number("dr-below", "-1000000", "1000000");
  const forS = number("dr-for", "0", "86400");
  const condEntity = text("dr-cent", "sun.sun");
  const condState = text("dr-cstate", "below_horizon");
  const service = select("dr-svc", [["light.turn_on", t("draft.turn_on")], ["light.turn_off", t("draft.turn_off")]]);
  const lights = text("dr-lights", "light.hall, light.porch");
  const brightness = number("dr-bri", "1", "100");
  const mode = select("dr-mode", ["single", "restart", "queued", "parallel"].map((m) => [m, t(`draft.mode.${m}`)]));
  const cooldown = number("dr-cool", "0", "3600");
  const stateBox = field("dr-to", t("draft.to"), to);
  const numericBox = h("div", { class: "grid2" }, field("dr-above", t("draft.above"), above), field("dr-below", t("draft.below"), below));
  const out = h("div", { "aria-live": "polite" });

  const syncKind = () => {
    const numeric = kind.value === "numeric_state";
    stateBox.hidden = numeric;
    numericBox.hidden = !numeric;
  };
  kind.addEventListener("change", syncKind);
  syncKind();
  const syncService = () => { brightness.disabled = service.value !== "light.turn_on"; };
  service.addEventListener("change", syncService);

  const optNumber = (input: HTMLInputElement) => (input.value.trim() === "" ? undefined : Number(input.value));

  const showResult = (res: DraftResult, name: string) => {
    const findings = res.findings.length === 0
      ? h("p", {}, h("span", { class: "badge ok" }, t("autopilot.clean")), " ", h("span", { class: "muted" }, t("autopilot.clean_hint")))
      : h("ul", { class: "list" }, res.findings.map((f) => h("li", {}, h("div", { class: "item" },
          h("div", { class: "row spread" }, h("span", { class: "title" }, `${f.rule_id} · ${t(`rule_name.${f.rule_id}`)}`), h("span", { class: `badge lvl-${f.level}` }, t(`level.${f.level}`))),
          h("div", { class: "sub" }, lintMessage(ctx, f))))));
    const saved = h("div", { role: "status" });
    out.replaceChildren(
      h("h3", {}, t("draft.result")),
      h("p", { class: "muted" }, t("draft.review")),
      codeBlock(res.yaml),
      h("h3", {}, t("autopilot.findings")),
      findings,
      h("div", { class: "actions" },
        h("button", { class: "secondary", type: "button", onclick: () => download(`${name}.yaml`, res.yaml) }, t("draft.download")),
        h("button", {
          class: "primary", type: "button",
          onclick: async () => {
            try {
              await ctx.ws({ type: "ha_sentinel/import/create", content_utf8: res.yaml, content_hash: await contentHash(res.yaml), resource_kind: "automation", label: `${name}.yaml`, request_key: uuid4() });
              saved.replaceChildren(h("p", { class: "banner" }, t("draft.saved")));
            } catch (e) {
              saved.replaceChildren(h("p", { class: "banner error" }, ctx.errorText(e as WsError)));
            }
          },
        }, t("draft.save_import"))),
      saved,
    );
  };

  const form = h(
    "form",
    {
      onsubmit: async (e: Event) => {
        e.preventDefault();
        if (!alias.value.trim()) { alias.focus(); return; }
        const trigger: Record<string, unknown> = { platform: kind.value, entity_id: trigEntity.value.trim(), for_s: Math.trunc(optNumber(forS) ?? 0) };
        if (kind.value === "state") {
          if (to.value.trim()) trigger.to = to.value.trim();
        } else {
          trigger.above = optNumber(above) ?? null;
          trigger.below = optNumber(below) ?? null;
        }
        const conditions = condEntity.value.trim() && condState.value.trim() ? [{ entity_id: condEntity.value.trim(), state: condState.value.trim() }] : [];
        const action: Record<string, unknown> = { service: service.value, entity_ids: lights.value.split(",").map((s) => s.trim()).filter(Boolean) };
        if (service.value === "light.turn_on" && optNumber(brightness) !== undefined) action.brightness_pct = Math.trunc(optNumber(brightness) as number);
        try {
          const res = await ctx.ws<DraftResult>({
            type: "ha_sentinel/autopilot/draft", alias: alias.value.trim(), trigger, conditions, actions: [action],
            mode: mode.value, cooldown_s: Math.trunc(optNumber(cooldown) ?? 0),
          });
          showResult(res, alias.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "draft");
        } catch (e2) {
          out.replaceChildren(h("p", { class: "banner error", role: "alert" }, ctx.errorText(e2 as WsError)));
        }
      },
    },
    field("dr-alias", t("draft.alias"), alias),
    h("h3", {}, t("draft.when")),
    h("div", { class: "grid2" }, field("dr-kind", t("draft.trigger"), kind), field("dr-tent", t("draft.entity"), trigEntity)),
    stateBox,
    numericBox,
    field("dr-for", t("draft.for"), forS),
    h("h3", {}, t("draft.only_if")),
    h("div", { class: "grid2" }, field("dr-cent", t("draft.entity"), condEntity), field("dr-cstate", t("draft.state_is"), condState)),
    h("h3", {}, t("draft.then")),
    h("div", { class: "grid2" }, field("dr-svc", t("draft.action"), service), field("dr-bri", t("draft.brightness"), brightness)),
    field("dr-lights", t("draft.lights"), lights, t("draft.lights_hint")),
    h("div", { class: "grid2" }, field("dr-mode", t("draft.mode"), mode), field("dr-cool", t("draft.cooldown"), cooldown, t("draft.cooldown_hint"))),
    h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, t("draft.generate"))),
  );

  return h(
    "section",
    { class: "card", "aria-labelledby": "h-draft" },
    h("h2", { id: "h-draft" }, t("draft.title")),
    h("p", { class: "muted" }, t("draft.hint")),
    form,
    out,
  );
}
