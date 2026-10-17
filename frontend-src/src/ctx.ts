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
  mapRevision: number | null; // null = current graph
  exportScopes: string[];
  exportRedacted: boolean;
  exportJob: string | null;
  deleteScope: string;
  restore: import("./types.ts").RestorePreview | null;
  managedDraft: { path: string; label: string; exclusive: boolean };
  targetDraft: { label: string; host: string; port: string; kind: string; path: string; loopback: boolean; device: string };
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
    mapRevision: null,
    exportScopes: ["diagnostics", "incidents"],
    exportRedacted: true,
    exportJob: null,
    deleteScope: "observations",
    restore: null,
    managedDraft: { path: "ha_sentinel_managed/automations.yaml", label: "Managed automations", exclusive: false },
    targetDraft: { label: "", host: "", port: "80", kind: "tcp", path: "/", loopback: false, device: "" },
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
