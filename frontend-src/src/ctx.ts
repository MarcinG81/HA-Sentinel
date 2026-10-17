// Shared view context so feature views stay outside the panel class.

import type { Translate } from "./i18n.ts";
import type { GraphNode, WsError } from "./types.ts";

export interface E3State {
  resourceId: string | null;
  preview: { snapshotId: string; text: string; label: string; source: string } | null;
  diff: { from: string; to: string } | null;
  analyzeId: string | null;
  importDraft: { label: string; kind: string; content: string; redact: boolean };
  importError: WsError | null;
  focusedFinding: number | null;
  mapUid: string | null;
  mapDirection: "dependents" | "dependencies";
  mapDepth: number;
  mapQuery: string;
  mapHits: GraphNode[] | null;
  mapGrouping: boolean;
  exportScopes: string[];
  exportRedacted: boolean;
  exportJob: string | null;
  deleteScope: string;
}

export function initialE3State(): E3State {
  return {
    resourceId: null,
    preview: null,
    diff: null,
    analyzeId: null,
    importDraft: { label: "automations.yaml", kind: "automation", content: "", redact: false },
    importError: null,
    focusedFinding: null,
    mapUid: null,
    mapDirection: "dependents",
    mapDepth: 2,
    mapQuery: "",
    mapHits: null,
    mapGrouping: false,
    exportScopes: ["diagnostics", "incidents"],
    exportRedacted: true,
    exportJob: null,
    deleteScope: "observations",
  };
}

export interface Ctx {
  ws<T>(message: Record<string, unknown>): Promise<T>;
  /** Authenticated GET through the HA session; never puts a token in the URL. */
  fetch(path: string): Promise<Response>;
  t: Translate;
  date(iso: string | null, withOffset?: boolean): string;
  rerender(): void;
  toast(text: string): void;
  errorText(err: WsError): string;
  goto(view: "history" | "autopilot" | "map"): void;
  state: E3State;
}
