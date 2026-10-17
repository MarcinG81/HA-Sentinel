// HA Sentinel sidebar panel (spec 14). A dependency-free web component that talks only to
// the admin-only ha_sentinel/* WebSocket commands through the HA session.

import { type Ctx, initialE3State } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import { formatDate, translator, type Translate } from "./i18n.ts";
import { styles } from "./styles.ts";
import type {
  Capability,
  ChangeEvent,
  Diagnostics,
  Evidence,
  Hass,
  Incident,
  IncidentDetail,
  IncidentPage,
  Overview,
  Severity,
  Subject,
  WsError,
} from "./types.ts";
import { hypothesisLine, incidentTitle, renderCounterEvidence, renderHypotheses } from "./rca.ts";
import { renderDataCard, renderExportCard } from "./views_data.ts";
import { renderAutopilot, renderHistory, renderMap } from "./views_e3.ts";

type View = "overview" | "doctor" | "history" | "autopilot" | "map" | "network" | "diagnostics" | "settings";

const VIEWS: View[] = ["overview", "doctor", "history", "autopilot", "map", "network", "diagnostics", "settings"];
const SEVERITY_ORDER: Severity[] = ["critical", "high", "warning", "info"];
const MENU_ICON = "M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z";
const REFRESH_DEBOUNCE_MS = 400;

/** Views that only show live state; editing views are never rebuilt under the user. */
const LIVE_VIEWS: View[] = ["overview", "doctor"];

/** HA passes sub-paths of the panel URL as route.path, e.g. "/history". */
function viewFromPath(path: string | undefined): View {
  const v = (path ?? "").split("/").filter(Boolean)[0] as View;
  return VIEWS.includes(v) ? v : "overview";
}

class SentinelPanel extends HTMLElement {
  private _hass?: Hass;
  private _narrow = false;
  private root!: ShadowRoot;
  private main!: HTMLElement;
  private tabs: HTMLButtonElement[] = [];
  private menuBtn!: HTMLButtonElement;
  private view: View = "overview";
  private basePath = "/ha-sentinel";
  private incidentId: string | null = null;
  private showResolved = false;
  private stale = false;
  private started = false;
  private unsubscribe: (() => Promise<void>) | null = null;
  private refreshTimer: number | undefined;
  private renderToken = 0;
  private t: Translate = translator("en");
  private incidentPages: Incident[] = [];
  private nextCursor: string | null = null;
  private e3 = initialE3State();
  private ctx: Ctx = {
    ws: (m) => this.ws(m),
    fetch: (path) => this._hass!.fetchWithAuth(path),
    t: (k) => this.t(k),
    date: (iso, off) => this.date(iso, off),
    rerender: () => void this.render(),
    toast: (text) => this.toast(text),
    errorText: (err) => this.errorText(err),
    goto: (view) => this.select(view),
    state: this.e3,
  };

  set hass(hass: Hass) {
    const languageChanged = !this._hass || this._hass.language !== hass.language;
    this._hass = hass;
    if (languageChanged) {
      this.t = translator(hass.locale?.language ?? hass.language);
      if (this.root) this.buildShell();
    }
    if (!this.started && this.isConnected) void this.start();
  }

  set route(route: { prefix?: string; path?: string } | undefined) {
    if (route?.prefix) this.basePath = route.prefix;
    const view = viewFromPath(route?.path);
    if (view !== this.view) {
      if (this.main) this.select(view, null, false);
      else this.view = view;
    }
  }

  set narrow(value: boolean) {
    this._narrow = value;
    if (this.menuBtn) this.menuBtn.hidden = !value;
  }

  connectedCallback(): void {
    if (!this.root) {
      this.root = this.attachShadow({ mode: "open" });
      this.buildShell();
    }
    if (this._hass && !this.started) void this.start();
  }

  disconnectedCallback(): void {
    this.started = false;
    window.clearTimeout(this.refreshTimer);
    const unsub = this.unsubscribe;
    this.unsubscribe = null;
    if (unsub) void unsub().catch(() => undefined);
    this._hass?.connection.removeEventListener("disconnected", this.onDisconnected);
    this._hass?.connection.removeEventListener("ready", this.onReady);
  }

  // --- Lifecycle -----------------------------------------------------------------------

  private async start(): Promise<void> {
    const hass = this._hass;
    if (!hass) return;
    this.started = true;
    hass.connection.addEventListener("disconnected", this.onDisconnected);
    hass.connection.addEventListener("ready", this.onReady);
    try {
      this.unsubscribe = await hass.connection.subscribeMessage<ChangeEvent>(
        () => this.scheduleRefresh(),
        { type: "ha_sentinel/subscribe" },
      );
    } catch {
      // Live updates are optional; the panel still works with manual refreshes.
      this.unsubscribe = null;
    }
    void this.render();
  }

  private onDisconnected = (): void => {
    this.stale = true;
    void this.render();
  };

  private onReady = (): void => {
    this.stale = false;
    void this.render();
  };

  private scheduleRefresh(): void {
    window.clearTimeout(this.refreshTimer);
    this.refreshTimer = window.setTimeout(() => {
      // Never rebuild under an open dialog or in editing views (import form, search).
      if (!LIVE_VIEWS.includes(this.view) || this.incidentId) return;
      if (!this.root.querySelector("dialog[open]")) void this.render(true);
    }, REFRESH_DEBOUNCE_MS);
  }

  private ws<T>(message: Record<string, unknown>): Promise<T> {
    if (!this._hass) return Promise.reject({ code: "not_ready" });
    return this._hass.callWS<T>({ ...message, api_version: 1 });
  }

  // --- Shell ---------------------------------------------------------------------------

  private buildShell(): void {
    const t = this.t;
    this.root.replaceChildren();
    this.root.appendChild(h("style", {}, styles));
    this.menuBtn = h(
      "button",
      {
        class: "menu-btn",
        "aria-label": t("menu"),
        onclick: () =>
          this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })),
      },
    ) as HTMLButtonElement;
    this.menuBtn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${MENU_ICON}"></path></svg>`;
    this.menuBtn.hidden = !this._narrow;

    this.tabs = VIEWS.map(
      (v) =>
        h(
          "button",
          {
            role: "tab",
            id: `tab-${v}`,
            "aria-controls": "content",
            "aria-selected": String(v === this.view),
            tabindex: v === this.view ? "0" : "-1",
            onclick: () => this.select(v),
          },
          t(`tab.${v}`),
        ) as HTMLButtonElement,
    );
    const nav = h("nav", { role: "tablist", "aria-label": t("title"), onkeydown: (e: Event) => this.onTabKey(e as KeyboardEvent) }, this.tabs);
    const header = h("header", {}, h("div", { class: "toolbar" }, this.menuBtn, h("h1", {}, t("title"))), nav);
    this.main = h("main", { id: "content", role: "tabpanel", tabindex: "-1" });
    this.root.append(header, this.main);
    requestAnimationFrame(() => this.tabs[VIEWS.indexOf(this.view)]?.scrollIntoView({ block: "nearest", inline: "nearest" }));
    if (this.started) void this.render();
  }

  private onTabKey(e: KeyboardEvent): void {
    const idx = VIEWS.indexOf(this.view);
    let next = idx;
    if (e.key === "ArrowRight") next = (idx + 1) % VIEWS.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + VIEWS.length) % VIEWS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = VIEWS.length - 1;
    else return;
    e.preventDefault();
    this.select(VIEWS[next]);
    this.tabs[next].focus();
  }

  private select(view: View, incidentId: string | null = null, updateUrl = true): void {
    this.view = view;
    this.incidentId = incidentId;
    if (view !== "doctor" || incidentId === null) {
      this.incidentPages = [];
      this.nextCursor = null;
    }
    this.tabs.forEach((tab, i) => {
      const selected = VIEWS[i] === view;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      // Keep the active tab visible in the scrollable strip on narrow screens.
      if (selected) tab.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    this.main.setAttribute("aria-labelledby", `tab-${view}`);
    if (updateUrl) {
      // Bookmarkable sub-path; HA resolves it back to this panel with route.path on reload.
      const path = view === "overview" ? this.basePath : `${this.basePath}/${view}`;
      if (location.pathname !== path) history.replaceState(history.state, "", path);
    }
    void this.render();
  }

  // --- Rendering -----------------------------------------------------------------------

  private async render(background = false): Promise<void> {
    if (!this._hass || !this.main) return;
    const token = ++this.renderToken;
    if (!background) this.main.replaceChildren(h("div", { class: "center muted", role: "status" }, this.t("loading")));
    let content: HTMLElement;
    try {
      content = await this.renderView();
    } catch (err) {
      content = this.errorCard(err as WsError);
    }
    if (token !== this.renderToken) return; // a newer render superseded this one
    const banner = this.stale ? h("div", { class: "banner", role: "alert" }, this.t("stale")) : null;
    this.main.replaceChildren(...(banner ? [banner] : []), content);
  }

  private renderView(): Promise<HTMLElement> {
    switch (this.view) {
      case "overview":
        return this.renderOverview();
      case "doctor":
        return this.incidentId ? this.renderIncident(this.incidentId) : this.renderIncidents();
      case "diagnostics":
        return this.renderDiagnostics();
      case "settings":
        return this.renderSettingsView();
      case "history":
        return renderHistory(this.ctx);
      case "autopilot":
        return renderAutopilot(this.ctx);
      case "map":
        return renderMap(this.ctx);
      default:
        return this.renderModulePlaceholder(this.view);
    }
  }

  private errorCard(err: WsError): HTMLElement {
    const t = this.t;
    return h(
      "div",
      { class: "card center", role: "alert" },
      h("p", {}, t("error")),
      err?.code ? h("p", { class: "muted" }, h("code", {}, err.code)) : null,
      h("button", { class: "secondary", onclick: () => void this.render() }, t("retry")),
    );
  }

  private severityBadge(sev: Severity): HTMLElement {
    return h("span", { class: `badge sev-${sev}` }, this.t(`sev.${sev}`));
  }

  private subjectText(s: Subject): string {
    return s.name ?? s.entity_id ?? s.uid ?? "—";
  }

  private warmupText(iso: string): string {
    const minutes = Math.max(1, Math.ceil((new Date(iso).getTime() - Date.now()) / 60000));
    const hass = this._hass!;
    const time = new Intl.DateTimeFormat(hass.locale?.language ?? hass.language, {
      timeZone: hass.config.time_zone, hour: "2-digit", minute: "2-digit",
    }).format(new Date(iso));
    return this.t("coverage.warmup").replace("{time}", time).replace("{min}", String(minutes));
  }

  private date(iso: string | null, withOffset = false): string {
    const hass = this._hass!;
    return formatDate(iso, hass.locale?.language ?? hass.language, hass.config.time_zone, withOffset);
  }

  // --- Overview -------------------------------------------------------------------------

  private async renderOverview(): Promise<HTMLElement> {
    const t = this.t;
    const [ov, caps, page] = await Promise.all([
      this.ws<Overview>({ type: "ha_sentinel/overview" }),
      this.ws<{ capabilities: Capability[] }>({ type: "ha_sentinel/capabilities" }),
      this.ws<IncidentPage>({ type: "ha_sentinel/incidents/list", limit: 20 }),
    ]);
    const hl = ov.health;
    const warmup = hl.coverage_complete_at ? this.warmupText(hl.coverage_complete_at) : null;
    const partial = hl.score === null && hl.score_partial !== null;
    const healthBody =
      hl.units === 0
        ? h("p", { class: "muted" }, t("health.no_units"))
        : partial
          ? [
              // Missing data must never look like a healthy result (spec 1.2, 8.3).
              h("p", {}, h("span", { class: "badge sev-warning" }, t("health.partial"))),
              h("div", { class: "metric partial", "aria-describedby": "health-partial-hint" }, Math.round(hl.score_partial!), h("small", {}, "/ 100")),
              h("p", { class: "muted", id: "health-partial-hint" }, warmup ?? t("health.partial_hint")),
            ]
          : h("div", { class: "metric" }, hl.score ?? "—", h("small", {}, "/ 100"));
    const coverage = hl.coverage_pct;
    const top = [...page.items].sort(
      (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
    )[0];

    const metrics = h(
      "div",
      { class: "grid" },
      h("section", { class: "card", "aria-labelledby": "h-health" }, h("h2", { id: "h-health" }, t("health.title")), healthBody),
      h(
        "section",
        { class: "card", "aria-labelledby": "h-cov" },
        h("h2", { id: "h-cov" }, t("coverage.title")),
        h("div", { class: "metric" }, coverage === null ? "—" : Math.round(coverage), h("small", {}, "%")),
        warmup ? h("p", { class: "banner", role: "status" }, warmup) : null,
        h("p", { class: "muted" }, t("coverage.hint")),
      ),
      h(
        "section",
        { class: "card", "aria-labelledby": "h-inc" },
        h("h2", { id: "h-inc" }, t("incidents.active")),
        h("div", { class: "metric" }, ov.counters.active_incidents),
        top
          ? h("button", { class: "secondary", onclick: () => this.select("doctor", top.id) }, t("incidents.open_top"))
          : h("p", { class: "muted" }, t("incidents.empty_hint")),
      ),
      h(
        "section",
        { class: "card", "aria-labelledby": "h-ent" },
        h("h2", { id: "h-ent" }, t("entities.title")),
        h("div", { class: "metric" }, ov.counters.entities),
      ),
    );

    const modules = h(
      "section",
      { class: "card", "aria-labelledby": "h-mod" },
      h("h2", { id: "h-mod" }, t("modules.title")),
      h(
        "table",
        {},
        h("tbody", {}, Object.entries(ov.modules).map(([name, state]) => h("tr", {}, h("th", { scope: "row" }, name), h("td", {}, state)))),
      ),
      h("h3", {}, t("caps.title")),
      this.capabilityTable(caps.capabilities),
    );

    const incidents = h(
      "section",
      { class: "card", "aria-labelledby": "h-list" },
      h("h2", { id: "h-list" }, t("incidents.active")),
      this.incidentList(page.items.slice(0, 5)),
    );
    return h("div", { class: "stack" }, metrics, incidents, modules);
  }

  private capabilityTable(caps: Capability[]): HTMLElement {
    return h(
      "table",
      {},
      h(
        "tbody",
        {},
        caps.map((c) =>
          h(
            "tr",
            {},
            h("th", { scope: "row" }, h("code", {}, c.capability_id)),
            h("td", {}, h("span", { class: c.status === "supported" ? "ok" : "status" }, c.status), c.reason_code ? h("span", { class: "muted" }, ` (${c.reason_code})`) : null),
          ),
        ),
      ),
    );
  }

  // --- Doctor: incidents ------------------------------------------------------------------

  private incidentList(items: Incident[]): HTMLElement {
    const t = this.t;
    if (items.length === 0) {
      return h("div", { class: "center" }, h("p", {}, t("incidents.empty")), h("p", { class: "muted" }, t("incidents.empty_hint")));
    }
    return h(
      "ul",
      { class: "list" },
      items.map((i) =>
        h(
          "li",
          {},
          h(
            "button",
            { class: "item", onclick: () => this.select("doctor", i.id) },
            h("div", { class: "row spread" }, h("span", { class: "title" }, incidentTitle(this.ctx, i)), this.severityBadge(i.severity)),
            i.kind === "infrastructure"
              ? h("div", { class: "sub" }, t("incident.members").replace("{n}", String(i.member_count)))
              : h("div", { class: "sub" }, this.subjectText(i.subject), i.subject.entity_id && i.subject.name ? ` · ${i.subject.entity_id}` : ""),
            i.top_hypothesis && i.top_hypothesis.label !== "none"
              ? h("div", { class: "sub" }, hypothesisLine(this.ctx, i.top_hypothesis.label, i.top_hypothesis.candidate_label))
              : null,
            i.merged_into ? h("div", { class: "sub" }, t("incident.merged")) : null,
            h("div", { class: "sub" }, `${t(`status.${i.status}`)} · ${t("detail.first_seen")} ${this.date(i.opened_at)}`),
          ),
        ),
      ),
    );
  }

  private async renderIncidents(): Promise<HTMLElement> {
    const t = this.t;
    if (this.incidentPages.length === 0) {
      const page = await this.ws<IncidentPage>({ type: "ha_sentinel/incidents/list", limit: 50, active_only: !this.showResolved });
      this.incidentPages = page.items;
      this.nextCursor = page.next_cursor;
    }
    const toggle = h(
      "label",
      { class: "row" },
      h("input", {
        type: "checkbox",
        checked: this.showResolved,
        onchange: (e: Event) => {
          this.showResolved = (e.target as HTMLInputElement).checked;
          this.incidentPages = [];
          void this.render();
        },
      }),
      t("incidents.show_resolved"),
    );
    const more = this.nextCursor
      ? h("button", { class: "secondary", onclick: () => void this.loadMore() }, t("incidents.more"))
      : null;
    return h(
      "section",
      { class: "card", "aria-labelledby": "h-doc" },
      h("div", { class: "row spread" }, h("h2", { id: "h-doc" }, t("tab.doctor")), toggle),
      this.incidentList(this.incidentPages),
      more ? h("div", { class: "actions" }, more) : null,
    );
  }

  private async loadMore(): Promise<void> {
    if (!this.nextCursor) return;
    const page = await this.ws<IncidentPage>({
      type: "ha_sentinel/incidents/list",
      limit: 50,
      active_only: !this.showResolved,
      cursor: this.nextCursor,
    });
    this.incidentPages = [...this.incidentPages, ...page.items];
    this.nextCursor = page.next_cursor;
    void this.render(true);
  }

  private async renderIncident(id: string): Promise<HTMLElement> {
    const t = this.t;
    const inc = await this.ws<IncidentDetail>({ type: "ha_sentinel/incidents/get", incident_id: id });
    const partial = inc.findings.some((f) => f.coverage !== "complete");

    const back = h("button", { class: "link", onclick: () => this.select("doctor") }, `← ${t("detail.back")}`);
    const head = h(
      "section",
      { class: "card" },
      h("div", { class: "row spread" }, h("h2", {}, incidentTitle(this.ctx, inc)), this.severityBadge(inc.severity)),
      h(
        "dl",
        {},
        h("dt", {}, t("detail.status")),
        h("dd", {}, t(`status.${inc.status}`)),
        inc.kind === "infrastructure"
          ? [h("dt", {}, t("detail.effects")), h("dd", {}, t("incident.members").replace("{n}", String(inc.member_count)))]
          : [h("dt", {}, t("detail.entity")), h("dd", {}, this.subjectText(inc.subject), inc.subject.entity_id ? h("div", {}, h("code", {}, inc.subject.entity_id)) : null)],
        h("dt", {}, t("detail.opened")),
        h("dd", {}, this.date(inc.opened_at, true)),
        inc.resolved_at ? [h("dt", {}, t("detail.resolved")), h("dd", {}, this.date(inc.resolved_at, true))] : null,
        inc.suppressed_until
          ? [h("dt", {}, t("detail.suppressed_until")), h("dd", {}, this.date(inc.suppressed_until, true), inc.suppress_reason ? ` — ${inc.suppress_reason}` : "")]
          : null,
      ),
      this.incidentActions(inc),
    );

    // Missing data takes precedence over any reassuring status (spec 14.1).
    const coverageBanner = partial ? h("div", { class: "banner", role: "note" }, t("detail.coverage_partial")) : null;

    const observation = h(
      "section",
      { class: "card", "aria-labelledby": "h-obs" },
      h("h2", { id: "h-obs" }, t("detail.observation")),
      inc.findings.map((f) =>
        h(
          "div",
          {},
          inc.kind === "infrastructure" ? h("h3", {}, this.subjectText(f.subject)) : null,
          h("p", { class: "muted" }, `${f.rule_id} v${f.rule_version} · ${t(`status.${f.status === "active" ? "open" : f.status}`)} · ${t("detail.coverage")}: ${t(`cov.${f.coverage}`)}`),
          h("p", {}, `${t("detail.first_seen")} ${this.date(f.first_seen, true)}`),
          h("ul", { class: "list" }, f.evidence.map((e) => h("li", {}, this.evidenceRow(e)))),
        ),
      ),
    );
    const hypothesis = renderHypotheses(this.ctx, inc, () => void this.render(true));
    const counter = renderCounterEvidence(this.ctx, inc);
    const rec = h(
      "section",
      { class: "card", "aria-labelledby": "h-rec" },
      h("h2", { id: "h-rec" }, t("detail.recommendation")),
      h("p", {}, t(inc.kind === "infrastructure" ? "rec.infrastructure" : `rec.${inc.rule_id}`)),
    );
    return h("div", { class: "stack" }, back, coverageBanner, head, hypothesis, counter, observation, rec);
  }

  private evidenceRow(e: Evidence): HTMLElement {
    const t = this.t;
    if (e.evidence_expired) return h("div", { class: "item" }, h("div", { class: "sub" }, t("detail.evidence_expired")));
    const p = e.payload ?? {};
    let text = "";
    const av = (v: unknown) => (v === null || v === undefined ? "—" : t(`av.${String(v)}`));
    if ("from" in p || "to" in p) text = `${av(p.from)} → ${av(p.to)}`;
    else if ("availability" in p) text = av(p.availability);
    else if ("value" in p) text = `${String(p.value)} ${String(p.unit ?? "")}`;
    const label = e.kind ? t(`ev.${e.kind}`) : t("ev.unknown");
    return h(
      "div",
      { class: "item" },
      h("div", { class: "title" }, `${label}: ${text}`),
      h("div", { class: "sub" }, this.date(e.occurred_at ?? e.received_at, true)),
    );
  }

  private incidentActions(inc: IncidentDetail): HTMLElement | null {
    const t = this.t;
    const buttons: HTMLElement[] = [];
    if (inc.status === "open") {
      buttons.push(
        h("button", { class: "primary", onclick: (ev: Event) => void this.ack(inc, ev.currentTarget as HTMLButtonElement) }, t("action.ack")),
      );
    }
    if (inc.status === "open" || inc.status === "acknowledged") {
      buttons.push(
        h("button", { class: "secondary", onclick: (ev: Event) => this.openSuppress(inc, ev.currentTarget as HTMLButtonElement) }, t("action.suppress")),
      );
    }
    if (buttons.length === 0) return null;
    return h("div", {}, h("div", { class: "actions" }, buttons), inc.status === "open" ? h("p", { class: "muted" }, t("detail.ack_note")) : null);
  }

  private async ack(inc: Incident, button: HTMLButtonElement): Promise<void> {
    button.disabled = true;
    try {
      await this.ws({
        type: "ha_sentinel/incidents/ack",
        incident_id: inc.id,
        expected_revision: inc.revision,
        request_key: uuid4(),
      });
    } catch (err) {
      this.toast(this.errorText(err as WsError));
    }
    void this.render(true);
  }

  private openSuppress(inc: Incident, trigger: HTMLButtonElement): void {
    const t = this.t;
    const durations: [string, number][] = [
      ["suppress.h1", 3600],
      ["suppress.h4", 4 * 3600],
      ["suppress.h24", 24 * 3600],
      ["suppress.d7", 7 * 24 * 3600],
    ];
    const select = h("select", { id: "sup-dur" }, durations.map(([k, s]) => h("option", { value: String(s) }, t(k)))) as HTMLSelectElement;
    const reason = h("input", { id: "sup-reason", type: "text", maxlength: "200", required: true }) as HTMLInputElement;
    const submit = h("button", { class: "primary", type: "submit" }, t("action.suppress")) as HTMLButtonElement;
    const dialog = h(
      "dialog",
      { "aria-labelledby": "sup-title" },
      h(
        "form",
        { method: "dialog" },
        h("h2", { id: "sup-title" }, t("suppress.title")),
        h("p", { class: "muted" }, t("suppress.hint")),
        h("label", { for: "sup-dur" }, t("suppress.duration")),
        select,
        h("label", { for: "sup-reason" }, t("suppress.reason")),
        reason,
        h(
          "div",
          { class: "actions" },
          h("button", { class: "secondary", type: "button", onclick: () => dialog.close() }, t("action.cancel")),
          submit,
        ),
      ),
    ) as HTMLDialogElement;
    dialog.addEventListener("close", () => {
      dialog.remove();
      trigger.focus(); // return focus to the control that opened the dialog
    });
    dialog.querySelector("form")!.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!reason.value.trim()) {
        reason.focus();
        return;
      }
      submit.disabled = true;
      const until = new Date(Date.now() + Number(select.value) * 1000).toISOString();
      try {
        await this.ws({
          type: "ha_sentinel/incidents/suppress",
          incident_id: inc.id,
          expected_revision: inc.revision,
          until,
          reason: reason.value.trim(),
          request_key: uuid4(),
        });
      } catch (err) {
        this.toast(this.errorText(err as WsError));
      }
      dialog.close();
      void this.render(true);
    });
    this.root.appendChild(dialog);
    dialog.showModal();
    reason.focus();
  }

  private errorText(err: WsError): string {
    if (err?.code === "conflict") return this.t("err.conflict");
    if (err?.code === "rate_limited") return this.t("err.rate_limited");
    return this.t("err.generic");
  }

  private toast(text: string): void {
    const el = h("div", { class: "toast", role: "status" }, text);
    this.root.appendChild(el);
    window.setTimeout(() => el.remove(), 4000);
  }

  // --- Other views -----------------------------------------------------------------------

  private async renderModulePlaceholder(view: View): Promise<HTMLElement> {
    const t = this.t;
    const caps = await this.ws<{ capabilities: Capability[] }>({ type: "ha_sentinel/capabilities" });
    const relevant: Record<string, string[]> = {
      network: ["network.probe"],
    };
    const ids = relevant[view] ?? [];
    return h(
      "section",
      { class: "card", "aria-labelledby": "h-mod" },
      h("h2", { id: "h-mod" }, t(`tab.${view}`)),
      h("div", { class: "banner", role: "note" }, h("strong", {}, t("module.unavailable")), " — ", t(`module.${view}`)),
      h("h3", {}, t("caps.title")),
      this.capabilityTable(caps.capabilities.filter((c) => ids.includes(c.capability_id))),
    );
  }

  private async renderDiagnostics(): Promise<HTMLElement> {
    const t = this.t;
    const d = await this.ws<Diagnostics>({ type: "ha_sentinel/diagnostics" });
    const kv = (rows: [string, unknown][]) =>
      h("table", {}, h("tbody", {}, rows.map(([k, v]) => h("tr", {}, h("th", { scope: "row" }, k), h("td", {}, typeof v === "boolean" ? t(v ? "yes" : "no") : String(v ?? "—"))))));
    const mib = (d.storage.db_bytes / (1024 * 1024)).toFixed(1);
    return h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "grid" },
        h("section", { class: "card" }, h("h2", {}, t("diag.queues")), kv(Object.entries(d.queues))),
        h(
          "section",
          { class: "card" },
          h("h2", {}, t("diag.storage")),
          kv([
            ["mode", d.storage.mode],
            ["schema_version", d.storage.schema_version],
            ["db_size", `${mib} MiB`],
            ["observations", d.storage.observations],
          ]),
        ),
        h(
          "section",
          { class: "card" },
          h("h2", {}, t("diag.doctor")),
          kv([
            ["state", d.state],
            ["reason_code", d.reason_code],
            ["active_findings", d.doctor.active_findings],
            ["flapping_partial", d.doctor.flapping_partial],
            ["next_deadline", this.date(d.doctor.next_deadline, true)],
            ["started_at", this.date(d.started_at, true)],
          ]),
        ),
      ),
      renderExportCard(this.ctx),
      h(
        "section",
        { class: "card" },
        h("h2", {}, t("diag.gaps")),
        d.coverage_gaps.length > 5 ? h("p", { class: "muted" }, t("diag.gaps_more").replace("{n}", String(d.coverage_gaps.length - 5))) : null,
        d.coverage_gaps.length === 0
          ? h("p", { class: "muted" }, t("diag.no_gaps"))
          : h(
              "ul",
              { class: "list" },
              d.coverage_gaps.slice(0, 5).map((g) =>
                h(
                  "li",
                  {},
                  h(
                    "div",
                    { class: "item" },
                    h("div", { class: "title" }, `${this.date(g.from, true)} → ${g.to ? this.date(g.to, true) : "…"}`),
                    h("div", { class: "sub" }, `${g.source_id} · ${g.scope} · ${g.status} · ${g.reason}`),
                  ),
                ),
              ),
            ),
      ),
    );
  }

  private async renderSettingsView(): Promise<HTMLElement> {
    return h("div", { class: "stack" }, this.renderSettings(), await renderDataCard(this.ctx));
  }

  private renderSettings(): HTMLElement {
    const t = this.t;
    const path = "/config/integrations/integration/ha_sentinel";
    return h(
      "section",
      { class: "card" },
      h("h2", {}, t("tab.settings")),
      h("p", {}, t("settings.hint")),
      h(
        "a",
        {
          class: "secondary",
          href: path,
          onclick: (e: Event) => {
            // In-app navigation the way HA panels do it, without a full page reload.
            e.preventDefault();
            history.pushState(null, "", path);
            window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
          },
        },
        t("settings.open"),
      ),
    );
  }
}

if (!customElements.get("ha-sentinel-panel")) {
  customElements.define("ha-sentinel-panel", SentinelPanel);
}
