// Tiny DOM builder. All dynamic values are assigned through textContent or attributes,
// never innerHTML, so entity names containing HTML render as plain text (spec 16.1).

type Child = Node | string | number | null | undefined | false;
type Attrs = Record<string, string | number | boolean | null | undefined | EventListener>;

export function h(tag: string, attrs: Attrs = {}, ...children: (Child | Child[])[]): HTMLElement {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "class") {
      el.className = String(value);
    } else if (value === true) {
      el.setAttribute(key, "");
    } else {
      el.setAttribute(key, String(value));
    }
  }
  append(el, children);
  return el;
}

function append(el: HTMLElement, children: (Child | Child[])[]): void {
  for (const child of children) {
    if (Array.isArray(child)) {
      append(el, child);
    } else if (child instanceof Node) {
      el.appendChild(child);
    } else if (child !== null && child !== undefined && child !== false) {
      el.appendChild(document.createTextNode(String(child)));
    }
  }
}

/** UUID v4 that also works on plain-HTTP LAN installs where crypto.randomUUID is absent. */
export function uuid4(): string {
  const c = globalThis.crypto;
  if (typeof c.randomUUID === "function") return c.randomUUID();
  const b = c.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
