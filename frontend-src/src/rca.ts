// Probable-cause rendering (spec 9.2, 14.1). Observation, hypothesis, counter-evidence and
// recommendation stay visually separate; a hypothesis is never presented as a fact.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import type { Hypothesis, Incident, IncidentDetail, WsError } from "./types.ts";

const FEATURES: (keyof Hypothesis["features"])[] = ["T", "D", "P", "E", "K"];

export function incidentTitle(ctx: Ctx, i: Incident): string {
  if (i.kind === "infrastructure") {
    return ctx.t("incident.infrastructure").replace("{root}", i.root?.label ?? "—");
  }
  return ctx.t(`rule.${i.rule_id}`);
}

export function hypothesisLine(ctx: Ctx, label: Hypothesis["label"], candidate: string): string {
  return ctx.t(`hyp.${label}`).replace("{candidate}", candidate);
}

export function renderHypotheses(ctx: Ctx, inc: IncidentDetail, onChange: () => void): HTMLElement {
  const { t } = ctx;
  const body: (HTMLElement | null)[] = [];
  if (inc.hypotheses.length === 0) {
    body.push(h("p", { class: "muted" }, t("hyp.empty")));
  } else {
    body.push(h("p", { class: "muted" }, t("hyp.disclaimer")));
    inc.hypotheses.forEach((hyp, idx) => {
      const confirmed = inc.confirmed_cause?.candidate_uid === hyp.candidate_uid ? inc.confirmed_cause : null;
      body.push(
        h(
          "div",
          { class: "hyp" },
          h(
            "div",
            { class: "row spread" },
            h("span", { class: "title" }, `${idx + 1}. ${hypothesisLine(ctx, hyp.label, hyp.candidate_label)}`),
            h("span", { class: `badge hyp-${hyp.label}` }, `${t(`hyp.label.${hyp.label}`)} · ${hyp.score.toFixed(2)}`),
          ),
          h("div", { class: "sub" }, `${t(`node.${hyp.candidate_type}`)} · ${t("hyp.affected").replace("{a}", String(hyp.affected)).replace("{o}", String(hyp.observable))} · ${t(`hyp.source.${hyp.evidence_source.split(":")[0]}`)}`),
          h(
            "table",
            { class: "features" },
            h("caption", { class: "sr-only" }, t("hyp.features")),
            h("thead", {}, h("tr", {}, FEATURES.map((f) => h("th", { scope: "col", title: t(`hyp.f.${f}`) }, f)))),
            h("tbody", {}, h("tr", {}, FEATURES.map((f) => h("td", {}, hyp.features[f].toFixed(2))))),
          ),
          h("p", { class: "muted small-text" }, FEATURES.map((f) => `${f}: ${t(`hyp.f.${f}`)}`).join(" · ")),
          hyp.reasons.length ? h("p", { class: "muted" }, hyp.reasons.map((r) => t(`hyp.reason.${r}`)).join(" ")) : null,
          confirmed
            ? h("p", {}, h("span", { class: `badge ${confirmed.verdict === "confirmed" ? "ok" : "status"}` }, t(`hyp.verdict.${confirmed.verdict}`)), " ", h("span", { class: "muted" }, ctx.date(confirmed.at, true)))
            : null,
          inc.status !== "resolved"
            ? h(
                "div",
                { class: "row" },
                h("button", { class: "secondary small", onclick: (e: Event) => void verdict(ctx, inc, hyp.candidate_uid, "confirmed", e, onChange) }, t("hyp.confirm")),
                h("button", { class: "secondary small", onclick: (e: Event) => void verdict(ctx, inc, hyp.candidate_uid, "rejected", e, onChange) }, t("hyp.reject")),
              )
            : null,
        ),
      );
    });
  }
  return h("section", { class: "card", "aria-labelledby": "h-hyp" }, h("h2", { id: "h-hyp" }, t("detail.hypothesis")), body);
}

export function renderCounterEvidence(ctx: Ctx, inc: IncidentDetail): HTMLElement {
  const { t } = ctx;
  const labelOf = new Map(inc.hypotheses.map((x) => [x.candidate_uid, x.candidate_label]));
  const items = inc.counter_evidence;
  return h(
    "section",
    { class: "card", "aria-labelledby": "h-ctr" },
    h("h2", { id: "h-ctr" }, t("detail.counter")),
    items.length === 0
      ? h("p", { class: "muted" }, t("detail.no_counter"))
      : h(
          "ul",
          { class: "list" },
          items.map((c) =>
            h(
              "li",
              {},
              h(
                "div",
                { class: "item" },
                h("div", { class: "title" }, t(`ctr.${c.kind}`).replace("{count}", String(c.count ?? 0))),
                h("div", { class: "sub" }, `${t("ctr.for")} ${labelOf.get(c.candidate_uid) ?? c.candidate_uid}`),
                c.sample?.length ? h("div", { class: "sub" }, c.sample.join(", ")) : null,
              ),
            ),
          ),
        ),
  );
}

async function verdict(ctx: Ctx, inc: IncidentDetail, candidate: string, value: "confirmed" | "rejected", e: Event, onChange: () => void): Promise<void> {
  (e.currentTarget as HTMLButtonElement).disabled = true;
  try {
    await ctx.ws({
      type: "ha_sentinel/incidents/confirm_cause",
      incident_id: inc.id,
      expected_revision: inc.revision,
      candidate_uid: candidate,
      verdict: value,
      request_key: uuid4(),
    });
  } catch (err) {
    ctx.toast(ctx.errorText(err as WsError));
  }
  onChange();
}
