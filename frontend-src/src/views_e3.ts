// Views for configuration history, autopilot lint and the dependency map (spec 10-12, 14).
// Everything is read-only towards Home Assistant: import stores a snapshot in Sentinel,
// export downloads YAML, and nothing here writes HA configuration or calls services.

import type { Ctx } from "./ctx.ts";
import { h, uuid4 } from "./dom.ts";
import { contentHash } from "./sha256.ts";
import type {
  DiffResult,
  ExportText,
  GraphEdge,
  GraphNode,
  GraphQueryResult,
  GraphSearch,
  LintFinding,
  LintReport,
  Resource,
  SnapshotMeta,
  WsError,
} from "./types.ts";

const shortHash = (hash: string | null) => (hash ? hash.replace("sha256:", "").slice(0, 10) : "—");

function download(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/yaml;charset=utf-8" }));
  const a = h("a", { href: url, download: filename }) as HTMLAnchorElement;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function codeBlock(text: string, highlight?: { from: number; to: number }, window_ = 4): HTMLElement {
  const lines = text.split("\n");
  let start = 0;
  let end = lines.length;
  if (highlight) {
    start = Math.max(0, highlight.from - 1 - window_);
    end = Math.min(lines.length, highlight.to + window_);
  }
  const width = String(end).length;
  return h(
    "pre",
    { class: "code", tabindex: "0" },
    lines.slice(start, end).map((line, i) => {
      const n = start + i + 1;
      const hit = highlight && n >= highlight.from && n <= highlight.to;
      return h("span", { class: hit ? "line hit" : "line" }, h("span", { class: "ln", "aria-hidden": "true" }, String(n).padStart(width)), " ", line || " ", "\n");
    }),
  );
}

// --- Configuration history -------------------------------------------------------------

export async function renderHistory(ctx: Ctx): Promise<HTMLElement> {
  const { t, state } = ctx;
  if (state.diff) return renderDiff(ctx, state.diff.from, state.diff.to);
  if (state.preview) return renderPreview(ctx);
  if (state.resourceId) return renderResource(ctx, state.resourceId);

  const { items } = await ctx.ws<{ items: Resource[] }>({ type: "ha_sentinel/resources/list" });
  const list =
    items.length === 0
      ? h("p", { class: "muted" }, t("history.empty"))
      : h(
          "ul",
          { class: "list" },
          items.map((r) =>
            h(
              "li",
              {},
              h(
                "button",
                { class: "item", onclick: () => { state.resourceId = r.id; ctx.rerender(); } },
                h("div", { class: "row spread" }, h("span", { class: "title" }, r.label), h("span", { class: "badge status" }, t(`kind.${r.kind}`))),
                h("div", { class: "sub" }, `${t("history.snapshots")}: ${r.snapshots} · ${t("history.updated")} ${ctx.date(r.updated_at)} · ${t(`policy.${r.write_policy}`)}`),
              ),
            ),
          ),
        );
  return h(
    "div",
    { class: "stack" },
    h("div", { class: "banner", role: "note" }, t("history.scope_note")),
    importCard(ctx),
    h("section", { class: "card", "aria-labelledby": "h-res" }, h("h2", { id: "h-res" }, t("history.resources")), list),
  );
}

function importCard(ctx: Ctx): HTMLElement {
  const { t, state } = ctx;
  const draft = state.importDraft;
  const label = h("input", { id: "imp-label", type: "text", maxlength: "120", value: draft.label, oninput: (e: Event) => { draft.label = (e.target as HTMLInputElement).value; } }) as HTMLInputElement;
  const kind = h(
    "select",
    { id: "imp-kind", onchange: (e: Event) => { draft.kind = (e.target as HTMLSelectElement).value; } },
    ["automation", "script", "blueprint"].map((k) => h("option", { value: k, selected: draft.kind === k }, t(`kind.${k}`))),
  ) as HTMLSelectElement;
  const area = h("textarea", { id: "imp-content", rows: "10", spellcheck: "false", placeholder: t("import.placeholder"), oninput: (e: Event) => { draft.content = (e.target as HTMLTextAreaElement).value; } }) as HTMLTextAreaElement;
  area.value = draft.content;
  const redact = h("input", { type: "checkbox", id: "imp-redact", checked: draft.redact, onchange: (e: Event) => { draft.redact = (e.target as HTMLInputElement).checked; } });
  const submit = h("button", { class: "primary", type: "submit" }, t("import.submit")) as HTMLButtonElement;

  const err = state.importError;
  const errorBox = err
    ? h(
        "div",
        { class: "banner error", role: "alert" },
        h(
          "div",
          {},
          h("strong", {}, t(`import.reason.${err.details?.reason ?? "generic"}`)),
          err.details?.line ? h("div", {}, `${t("import.line")} ${err.details.line}`) : null,
          err.details?.paths?.length
            ? h("div", {}, t("import.secret_paths"), h("ul", {}, err.details.paths.map((p) => h("li", {}, h("code", {}, p)))))
            : null,
        ),
      )
    : null;

  const form = h(
    "form",
    {
      onsubmit: async (e: Event) => {
        e.preventDefault();
        if (!draft.content.trim()) { area.focus(); return; }
        submit.disabled = true;
        try {
          const res = await ctx.ws<{ resource_id: string; deduplicated: boolean; redacted_paths: string[] }>({
            type: "ha_sentinel/import/create",
            content_utf8: draft.content,
            content_hash: await contentHash(draft.content),
            resource_kind: draft.kind,
            label: draft.label.trim() || "import.yaml",
            redact_secrets: draft.redact,
            request_key: uuid4(),
          });
          state.importError = null;
          draft.content = "";
          ctx.toast(res.deduplicated ? t("import.deduplicated") : t("import.done"));
          state.resourceId = res.resource_id;
        } catch (e2) {
          const we = e2 as WsError;
          state.importError = we.code === "validation_failed" ? we : null;
          if (we.code !== "validation_failed") ctx.toast(ctx.errorText(we));
        }
        ctx.rerender();
      },
    },
    h("div", { class: "grid2" },
      h("div", {}, h("label", { for: "imp-label" }, t("import.label")), label),
      h("div", {}, h("label", { for: "imp-kind" }, t("import.kind")), kind)),
    h("label", { for: "imp-content" }, t("import.content")),
    area,
    h("label", { class: "row check", for: "imp-redact" }, redact, t("import.redact")),
    h("p", { class: "muted" }, t("import.privacy")),
    errorBox,
    h("div", { class: "actions" }, submit),
  );
  return h("section", { class: "card", "aria-labelledby": "h-imp" }, h("h2", { id: "h-imp" }, t("import.title")), form);
}

async function renderResource(ctx: Ctx, resourceId: string): Promise<HTMLElement> {
  const { t, state } = ctx;
  const { items } = await ctx.ws<{ items: SnapshotMeta[] }>({ type: "ha_sentinel/snapshots/list", resource_id: resourceId });
  const back = h("button", { class: "link", onclick: () => { state.resourceId = null; ctx.rerender(); } }, `← ${t("history.back")}`);
  const rows = items.map((s, i) => {
    const prev = items[i + 1];
    const actions = h(
      "div",
      { class: "row" },
      h("button", { class: "secondary small", onclick: () => void openPreview(ctx, s.id) }, t("history.preview")),
      h("button", { class: "secondary small", onclick: () => void downloadSnapshot(ctx, s.id) }, t("history.download")),
      prev ? h("button", { class: "secondary small", onclick: () => { state.diff = { from: prev.id, to: s.id }; ctx.rerender(); } }, t("history.diff_prev")) : null,
      s.validation.runnables !== undefined
        ? h("button", { class: "secondary small", onclick: () => { state.analyzeId = s.id; ctx.goto("autopilot"); } }, t("history.analyze"))
        : null,
    );
    return h(
      "li",
      {},
      h(
        "div",
        { class: "item" },
        h("div", { class: "row spread" }, h("span", { class: "title" }, ctx.date(s.created_at, true)), i === 0 ? h("span", { class: "badge ok" }, t("history.latest")) : null),
        h("div", { class: "sub" }, `${t("history.canonical")} ${shortHash(s.canonical_hash)} · ${t("history.raw")} ${s.raw_available ? shortHash(s.raw_hash) : t("history.raw_not_kept")}`),
        s.redacted_paths.length ? h("div", { class: "sub" }, h("span", { class: "badge sev-warning" }, t("history.redacted")), ` ${s.redacted_paths.length}`) : null,
        h("div", { class: "sub" }, t("history.not_restorable")),
        actions,
      ),
    );
  });
  return h(
    "div",
    { class: "stack" },
    back,
    h("section", { class: "card", "aria-labelledby": "h-snap" }, h("h2", { id: "h-snap" }, t("history.snapshots")), h("ul", { class: "list" }, rows)),
  );
}

async function openPreview(ctx: Ctx, snapshotId: string): Promise<void> {
  try {
    const exp = await ctx.ws<ExportText>({ type: "ha_sentinel/snapshots/get", snapshot_id: snapshotId });
    ctx.state.preview = { snapshotId, text: exp.content, label: exp.label, source: exp.source };
    ctx.rerender();
  } catch (e) {
    ctx.toast(ctx.errorText(e as WsError));
  }
}

async function downloadSnapshot(ctx: Ctx, snapshotId: string): Promise<void> {
  try {
    const exp = await ctx.ws<ExportText>({ type: "ha_sentinel/snapshots/get", snapshot_id: snapshotId });
    const base = exp.label.replace(/[^\w.-]+/g, "_").replace(/\.ya?ml$/i, "");
    download(`${base}.${snapshotId.slice(0, 8)}.yaml`, exp.content);
  } catch (e) {
    ctx.toast(ctx.errorText(e as WsError));
  }
}

function renderPreview(ctx: Ctx): Promise<HTMLElement> {
  const { t, state } = ctx;
  const p = state.preview!;
  return Promise.resolve(
    h(
      "div",
      { class: "stack" },
      h("button", { class: "link", onclick: () => { state.preview = null; ctx.rerender(); } }, `← ${t("history.back_snapshots")}`),
      h(
        "section",
        { class: "card" },
        h("div", { class: "row spread" }, h("h2", {}, p.label), h("button", { class: "secondary small", onclick: () => download(`${p.label.replace(/\.ya?ml$/i, "")}.yaml`, p.text) }, t("history.download"))),
        p.source === "rendered" ? h("p", { class: "muted" }, t("history.rendered_note")) : null,
        codeBlock(p.text),
        h("p", { class: "muted" }, t("history.manual_restore")),
      ),
    ),
  );
}

async function renderDiff(ctx: Ctx, from: string, to: string): Promise<HTMLElement> {
  const { t, state } = ctx;
  const d = await ctx.ws<DiffResult>({ type: "ha_sentinel/snapshots/diff", from_snapshot_id: from, to_snapshot_id: to });
  const fmt = (v: unknown) => (v === undefined ? "" : JSON.stringify(v));
  const summary = !d.semantic_changed
    ? d.textual_changed ? t("diff.textual_only") : t("diff.identical")
    : t("diff.semantic");
  const semantic =
    d.semantic.length === 0
      ? h("p", { class: "muted" }, t("diff.no_semantic"))
      : h(
          "table",
          { class: "diff" },
          h("thead", {}, h("tr", {}, h("th", { scope: "col" }, t("diff.op")), h("th", { scope: "col" }, t("diff.path")), h("th", { scope: "col" }, t("diff.change")))),
          h(
            "tbody",
            {},
            d.semantic.map((c) =>
              h(
                "tr",
                {},
                h("td", {}, h("span", { class: `badge op-${c.op}` }, t(`diff.op.${c.op}`))),
                h("td", {}, h("code", {}, c.path)),
                h("td", {}, c.op !== "add" ? h("div", { class: "old" }, h("code", {}, `− ${fmt(c.old)}`)) : null, c.op !== "remove" ? h("div", { class: "new" }, h("code", {}, `+ ${fmt(c.new)}`)) : null),
              ),
            ),
          ),
        );
  const textual = d.textual
    ? h(
        "pre",
        { class: "code", tabindex: "0" },
        d.textual.map((line) =>
          h("span", { class: line.startsWith("+") && !line.startsWith("+++") ? "line add" : line.startsWith("-") && !line.startsWith("---") ? "line del" : "line" }, line, "\n"),
        ),
      )
    : h("p", { class: "muted" }, t("diff.no_textual"));
  return h(
    "div",
    { class: "stack" },
    h("button", { class: "link", onclick: () => { state.diff = null; ctx.rerender(); } }, `← ${t("history.back_snapshots")}`),
    h("div", { class: "banner", role: "status" }, summary),
    h("section", { class: "card" }, h("h2", {}, t("diff.semantic_title")), semantic),
    h("section", { class: "card" }, h("h2", {}, t("diff.textual_title")), textual),
  );
}

// --- Autopilot ----------------------------------------------------------------------------

const LEVEL_ORDER = ["error", "risk", "warning", "unsupported", "info"];

export async function renderAutopilot(ctx: Ctx): Promise<HTMLElement> {
  const { t, state } = ctx;
  const { items } = await ctx.ws<{ items: Resource[] }>({ type: "ha_sentinel/resources/list" });
  const lintable = items.filter((r) => r.kind !== "sentinel_options");
  if (lintable.length === 0) {
    return h(
      "section",
      { class: "card center" },
      h("p", {}, t("autopilot.empty")),
      h("button", { class: "secondary", onclick: () => ctx.goto("history") }, t("autopilot.go_import")),
    );
  }
  const options: HTMLElement[] = [];
  const latestIds: Record<string, string> = {};
  for (const r of lintable) {
    const { items: snaps } = await ctx.ws<{ items: SnapshotMeta[] }>({ type: "ha_sentinel/snapshots/list", resource_id: r.id, limit: 1 });
    if (snaps[0]) {
      latestIds[r.id] = snaps[0].id;
      options.push(h("option", { value: snaps[0].id, selected: state.analyzeId === snaps[0].id }, `${r.label} (${t(`kind.${r.kind}`)})`));
    }
  }
  if (!state.analyzeId) state.analyzeId = Object.values(latestIds)[0] ?? null;
  const picker = h(
    "select",
    { id: "ap-snap", onchange: (e: Event) => { state.analyzeId = (e.target as HTMLSelectElement).value; state.focusedFinding = null; ctx.rerender(); } },
    options,
  );
  const head = h(
    "section",
    { class: "card" },
    h("h2", {}, t("tab.autopilot")),
    h("label", { for: "ap-snap" }, t("autopilot.pick")),
    picker,
    h("p", { class: "muted" }, t("autopilot.scope")),
  );
  if (!state.analyzeId) return h("div", { class: "stack" }, head);

  const [report, exp] = await Promise.all([
    ctx.ws<LintReport>({ type: "ha_sentinel/autopilot/analyze", snapshot_id: state.analyzeId }),
    ctx.ws<ExportText>({ type: "ha_sentinel/snapshots/get", snapshot_id: state.analyzeId }),
  ]);
  const pct = Math.round(report.completeness * 100);
  const counts = LEVEL_ORDER.map((lvl) => [lvl, report.findings.filter((f) => f.level === lvl).length] as const).filter(([, n]) => n > 0);
  const stats = h(
    "div",
    { class: "grid" },
    h("section", { class: "card" }, h("h2", {}, t("autopilot.completeness")), h("div", { class: `metric${pct < 100 ? " partial" : ""}` }, pct, h("small", {}, "%")), h("p", { class: "muted" }, t("autopilot.completeness_hint"))),
    h("section", { class: "card" }, h("h2", {}, t("autopilot.runnables")), h("div", { class: "metric" }, report.runnables.length), report.truncated ? h("p", { class: "muted" }, t("autopilot.truncated")) : null),
    h(
      "section",
      { class: "card" },
      h("h2", {}, t("autopilot.findings")),
      counts.length === 0
        ? h("p", {}, h("span", { class: "badge ok" }, t("autopilot.clean")), " ", h("span", { class: "muted" }, t("autopilot.clean_hint")))
        : h("div", { class: "row" }, counts.map(([lvl, n]) => h("span", { class: `badge lvl-${lvl}` }, `${t(`level.${lvl}`)}: ${n}`))),
    ),
  );
  const sorted = [...report.findings].sort((a, b) => LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level) || (a.span?.line ?? 0) - (b.span?.line ?? 0));
  const findings = h(
    "section",
    { class: "card", "aria-labelledby": "h-lint" },
    h("h2", { id: "h-lint" }, t("autopilot.findings")),
    !report.inventory_complete ? h("p", { class: "muted" }, t("autopilot.inventory_partial")) : null,
    sorted.length === 0
      ? h("p", { class: "muted" }, t("autopilot.clean_hint"))
      : h("ul", { class: "list" }, sorted.map((f, i) => lintItem(ctx, f, i, exp.content, report.spans_available))),
  );
  return h("div", { class: "stack" }, head, stats, findings);
}

function lintMessage(ctx: Ctx, f: LintFinding): string {
  let text = ctx.t(`lint.${f.message_key}`);
  for (const [k, v] of Object.entries(f.params)) {
    text = text.replaceAll(`{${k}}`, Array.isArray(v) ? v.join(" → ") : String(v));
  }
  return text;
}

function lintItem(ctx: Ctx, f: LintFinding, index: number, source: string, spans: boolean): HTMLElement {
  const { t, state } = ctx;
  const open = state.focusedFinding === index;
  const where = f.span ? `${t("autopilot.line")} ${f.span.line}:${f.span.column}` : f.pointer || "/";
  return h(
    "li",
    {},
    h(
      "button",
      { class: "item", "aria-expanded": String(open), onclick: () => { state.focusedFinding = open ? null : index; ctx.rerender(); } },
      h("div", { class: "row spread" }, h("span", { class: "title" }, `${f.rule_id} · ${t(`rule_name.${f.rule_id}`)}`), h("span", { class: `badge lvl-${f.level}` }, t(`level.${f.level}`))),
      h("div", { class: "sub" }, lintMessage(ctx, f)),
      h("div", { class: "sub" }, f.runnable ? `${f.runnable} · ` : "", where, f.heuristic ? ` · ${t("autopilot.heuristic")}` : ""),
    ),
    open && spans && f.span ? codeBlock(source, { from: f.span.line, to: f.span.end_line }) : null,
    open && !spans ? h("p", { class: "muted" }, t("autopilot.no_spans")) : null,
  );
}

// --- Dependency map --------------------------------------------------------------------------

export async function renderMap(ctx: Ctx): Promise<HTMLElement> {
  const { t, state } = ctx;
  const input = h("input", { id: "map-q", type: "search", value: state.mapQuery, placeholder: t("map.placeholder"), oninput: (e: Event) => { state.mapQuery = (e.target as HTMLInputElement).value; } }) as HTMLInputElement;
  const search = h(
    "form",
    {
      class: "row",
      role: "search",
      onsubmit: async (e: Event) => {
        e.preventDefault();
        if (!state.mapQuery.trim()) return;
        const res = await ctx.ws<GraphSearch>({ type: "ha_sentinel/graph/search", q: state.mapQuery.trim() });
        state.mapHits = res.items;
        ctx.rerender();
      },
    },
    h("label", { for: "map-q", class: "sr-only" }, t("map.search")),
    h("div", { class: "grow" }, input),
    h("button", { class: "primary", type: "submit" }, t("map.search")),
  );
  const hits = state.mapHits
    ? state.mapHits.length === 0
      ? h("p", { class: "muted" }, t("map.no_hits"))
      : h("ul", { class: "list" }, state.mapHits.map((n) => h("li", {}, h("button", { class: "item", onclick: () => { state.mapUid = n.uid; ctx.rerender(); } }, nodeLine(ctx, n)))))
    : null;
  const parts: HTMLElement[] = [h("section", { class: "card" }, h("h2", {}, t("tab.map")), h("p", { class: "muted" }, t("map.intro")), search, hits)];
  if (state.mapUid) parts.push(await renderNeighbourhood(ctx, state.mapUid));
  return h("div", { class: "stack" }, parts);
}

function nodeLine(ctx: Ctx, n: GraphNode): HTMLElement[] {
  const { t } = ctx;
  return [
    h("div", { class: "row spread" }, h("span", { class: "title" }, n.label), h("span", { class: `badge type-${n.type}` }, t(`node.${n.type}`))),
    n.external_ref && n.external_ref !== n.label ? h("div", { class: "sub" }, h("code", {}, n.external_ref)) : h("div", {}),
  ];
}

async function renderNeighbourhood(ctx: Ctx, uid: string): Promise<HTMLElement> {
  const { t, state } = ctx;
  let res: GraphQueryResult;
  try {
    res = await ctx.ws<GraphQueryResult>({ type: "ha_sentinel/graph/query", uid, direction: state.mapDirection, depth: state.mapDepth, include_grouping: state.mapGrouping });
  } catch (e) {
    if ((e as WsError).code === "not_found") {
      state.mapUid = null;
      return h("div", { class: "banner", role: "alert" }, t("map.gone"));
    }
    throw e;
  }
  const byUid = new Map(res.nodes.map((n) => [n.uid, n]));
  const root = byUid.get(uid)!;
  const controls = h(
    "div",
    { class: "row" },
    h(
      "div",
      { role: "radiogroup", "aria-label": t("map.direction"), class: "row" },
      (["dependents", "dependencies"] as const).map((dir) =>
        h("button", { class: state.mapDirection === dir ? "primary small" : "secondary small", role: "radio", "aria-checked": String(state.mapDirection === dir), onclick: () => { state.mapDirection = dir; ctx.rerender(); } }, t(`map.${dir}`)),
      ),
    ),
    h("label", { class: "row check" }, t("map.depth"), h("select", { onchange: (e: Event) => { state.mapDepth = Number((e.target as HTMLSelectElement).value); ctx.rerender(); } }, [1, 2, 3, 4, 5].map((d) => h("option", { value: String(d), selected: d === state.mapDepth }, String(d))))),
    h("label", { class: "row check" }, h("input", { type: "checkbox", checked: state.mapGrouping, onchange: (e: Event) => { state.mapGrouping = (e.target as HTMLInputElement).checked; ctx.rerender(); } }), t("map.grouping")),
  );
  const edgeRow = (e: GraphEdge) => {
    const other = byUid.get(state.mapDirection === "dependents" ? e.from : e.to);
    const fromN = byUid.get(e.from);
    const toN = byUid.get(e.to);
    return h(
      "tr",
      {},
      h("td", {}, other ? h("button", { class: "link", onclick: () => { state.mapUid = other.uid; ctx.rerender(); } }, other.label) : "—", other ? h("div", { class: "sub" }, t(`node.${other.type}`)) : null),
      h("td", {}, `${fromN?.label ?? e.from} → ${t(`rel.${e.relation}`)} → ${toN?.label ?? e.to}`),
      h("td", {}, t(`prov.${e.provenance}`), h("div", { class: "sub" }, `${t("map.confidence")} ${e.confidence.toFixed(2)}`)),
    );
  };
  const incompleteSources = res.nodes.filter((n) => n.metadata && n.metadata.complete === false);
  return h(
    "section",
    { class: "card", "aria-labelledby": "h-nb" },
    h("div", { class: "row spread" }, h("h2", { id: "h-nb" }, root.label), h("span", { class: `badge type-${root.type}` }, t(`node.${root.type}`))),
    root.external_ref ? h("p", {}, h("code", {}, root.external_ref)) : null,
    controls,
    res.truncated ? h("div", { class: "banner", role: "note" }, t("map.truncated")) : null,
    incompleteSources.length ? h("div", { class: "banner", role: "note" }, t("map.incomplete")) : null,
    res.unresolved_count ? h("p", { class: "muted" }, `${t("map.unresolved")}: ${res.unresolved_count}`) : null,
    res.edges.length === 0
      ? h("p", { class: "muted" }, t(state.mapDirection === "dependents" ? "map.no_dependents" : "map.no_dependencies"))
      : h(
          "table",
          { class: "edges" },
          h("caption", { class: "sr-only" }, t(`map.${state.mapDirection}`)),
          h("thead", {}, h("tr", {}, h("th", { scope: "col" }, t("map.object")), h("th", { scope: "col" }, t("map.relation")), h("th", { scope: "col" }, t("map.source")))),
          h("tbody", {}, res.edges.map(edgeRow)),
        ),
    h("p", { class: "muted" }, `${t("map.revision")} ${res.graph_revision} · ${ctx.date(res.built_at, true)}`),
  );
}
