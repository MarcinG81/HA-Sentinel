// Uses HA theme variables so the panel follows light/dark themes without its own palette.

export const styles = `
:host {
  display: block;
  min-height: 100vh;
  background: var(--primary-background-color);
  color: var(--primary-text-color);
  font-family: var(--paper-font-body1_-_font-family, var(--ha-font-family-body, Roboto, sans-serif));
  -webkit-font-smoothing: antialiased;
}
* { box-sizing: border-box; }
header {
  position: sticky; top: 0; z-index: 2;
  background: var(--app-header-background-color, var(--primary-color));
  color: var(--app-header-text-color, var(--text-primary-color, #fff));
  border-bottom: 1px solid var(--app-header-border-bottom, transparent);
}
.toolbar { display: flex; align-items: center; height: 56px; padding: 0 12px; gap: 8px; }
.toolbar h1 { font-size: 20px; font-weight: 400; margin: 0 0 0 4px; flex: 1; }
.menu-btn {
  width: 40px; height: 40px; border: 0; border-radius: 50%;
  background: transparent; color: inherit; cursor: pointer; display: grid; place-items: center;
}
.menu-btn[hidden] { display: none; }
.menu-btn:hover { background: rgba(127,127,127,.15); }
.menu-btn svg { width: 24px; height: 24px; fill: currentColor; }
nav[role=tablist] {
  display: flex; overflow-x: auto; scrollbar-width: none; padding: 0 4px;
}
nav[role=tablist]::-webkit-scrollbar { display: none; }
[role=tab] {
  flex: none; background: none; border: 0; color: inherit; opacity: .75;
  padding: 12px 14px; font: inherit; font-size: 14px; cursor: pointer;
  border-bottom: 2px solid transparent; white-space: nowrap;
}
[role=tab][aria-selected=true] { opacity: 1; border-bottom-color: currentColor; font-weight: 500; }
[role=tab]:focus-visible, button:focus-visible, a:focus-visible, select:focus-visible, input:focus-visible {
  outline: 2px solid var(--primary-color); outline-offset: 2px;
}
main { padding: 16px; max-width: 1100px; margin: 0 auto; }
.banner {
  display: flex; gap: 8px; align-items: flex-start;
  padding: 12px 14px; border-radius: 12px; margin-bottom: 16px;
  background: var(--card-background-color); border-left: 4px solid var(--warning-color, #ffa600);
}
.banner.error { border-left-color: var(--error-color, #db4437); }
.banner.ok { border-left-color: var(--success-color, #43a047); }
.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.card {
  background: var(--card-background-color, #fff);
  border-radius: var(--ha-card-border-radius, 12px);
  border: 1px solid var(--divider-color, rgba(0,0,0,.12));
  padding: 16px; min-width: 0;
}
.card h2 { font-size: 16px; font-weight: 500; margin: 0 0 12px; }
.card h3 { font-size: 14px; font-weight: 500; margin: 16px 0 8px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
.stack { display: flex; flex-direction: column; gap: 16px; }
.metric { font-size: 40px; font-weight: 300; line-height: 1.1; }
.metric.partial { color: var(--secondary-text-color); }
.metric small { font-size: 16px; color: var(--secondary-text-color); margin-left: 4px; }
.muted { color: var(--secondary-text-color); font-size: 14px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.spread { justify-content: space-between; }
.badge {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 500; padding: 2px 10px; border-radius: 999px;
  border: 1px solid currentColor; white-space: nowrap;
}
.badge::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.sev-critical { color: var(--error-color, #db4437); }
.sev-high { color: var(--error-color, #db4437); }
.sev-warning { color: var(--warning-color, #ffa600); }
.sev-info { color: var(--info-color, #039be5); }
.ok { color: var(--success-color, #43a047); }
.status { color: var(--secondary-text-color); }
ul.list { list-style: none; margin: 0; padding: 0; }
ul.list > li { border-top: 1px solid var(--divider-color); }
ul.list > li:first-child { border-top: 0; }
.item {
  display: block; width: 100%; text-align: left; background: none; border: 0; color: inherit;
  font: inherit; padding: 12px 4px; cursor: pointer; border-radius: 8px;
}
.item:hover { background: rgba(127,127,127,.08); }
.item .title { font-weight: 500; overflow-wrap: anywhere; }
.item .sub { color: var(--secondary-text-color); font-size: 13px; overflow-wrap: anywhere; margin-top: 2px; }
button.primary, button.secondary, a.secondary {
  font: inherit; font-size: 14px; font-weight: 500; border-radius: 20px; padding: 8px 18px;
  cursor: pointer; border: 1px solid var(--primary-color); text-decoration: none; display: inline-block;
}
button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
button.secondary, a.secondary { background: transparent; color: var(--primary-color); }
button[disabled] { opacity: .5; cursor: default; }
.link { align-self: flex-start; background: none; border: 0; color: var(--primary-color); font: inherit; cursor: pointer; padding: 4px 0; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 6px 4px; border-top: 1px solid var(--divider-color); vertical-align: top; overflow-wrap: anywhere; }
th { color: var(--secondary-text-color); font-weight: 500; width: 45%; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 6px 12px; margin: 0; font-size: 14px; }
dt { color: var(--secondary-text-color); }
dd { margin: 0; overflow-wrap: anywhere; }
code { font-family: var(--code-font-family, monospace); font-size: 13px; overflow-wrap: anywhere; }
.center { text-align: center; padding: 32px 8px; }
dialog {
  border: 0; border-radius: 16px; padding: 24px; width: min(420px, calc(100vw - 32px));
  background: var(--card-background-color); color: var(--primary-text-color);
}
dialog::backdrop { background: rgba(0,0,0,.4); }
dialog h2 { margin: 0 0 8px; font-size: 18px; font-weight: 500; }
label { display: block; font-size: 14px; margin: 12px 0 4px; color: var(--secondary-text-color); }
select, input[type=text] {
  width: 100%; font: inherit; padding: 8px 10px; border-radius: 8px;
  border: 1px solid var(--divider-color); background: var(--primary-background-color); color: inherit;
}
.actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 20px; flex-wrap: wrap; }
.toast { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); background: var(--primary-text-color); color: var(--primary-background-color); padding: 10px 16px; border-radius: 8px; font-size: 14px; max-width: calc(100vw - 32px); z-index: 5; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.grid2 { display: grid; gap: 12px; grid-template-columns: 2fr 1fr; }
.grow { flex: 1; min-width: 0; }
.check { display: flex; align-items: center; gap: 8px; color: var(--primary-text-color); margin: 12px 0 0; }
.check select { width: auto; }
textarea, input[type=search], input[type=number] {
  width: 100%; font: inherit; padding: 8px 10px; border-radius: 8px;
  border: 1px solid var(--divider-color); background: var(--primary-background-color); color: inherit;
}
textarea { font-family: var(--code-font-family, monospace); font-size: 13px; resize: vertical; }
button.small { padding: 4px 12px; font-size: 13px; margin-top: 8px; }
pre.code {
  margin: 8px 0 0; padding: 8px 0; overflow-x: auto; border-radius: 8px; font-size: 13px; line-height: 1.5;
  background: var(--secondary-background-color, rgba(127,127,127,.08));
  font-family: var(--code-font-family, monospace); max-height: 60vh;
}
pre.code .line { display: block; padding: 0 12px; white-space: pre; }
pre.code .ln { color: var(--secondary-text-color); user-select: none; margin-right: 8px; }
pre.code .hit { background: rgba(255,166,0,.22); box-shadow: inset 3px 0 var(--warning-color, #ffa600); }
pre.code .add { background: rgba(67,160,71,.18); }
pre.code .del { background: rgba(219,68,55,.16); }
table.diff td .old { color: var(--error-color, #db4437); }
table.diff td .new { color: var(--success-color, #43a047); }
table.diff th, table.edges th { width: auto; }
.op-add, .lvl-info { color: var(--info-color, #039be5); }
.op-remove, .lvl-error, .lvl-risk { color: var(--error-color, #db4437); }
.op-replace, .lvl-warning { color: var(--warning-color, #ffa600); }
.lvl-unsupported { color: var(--secondary-text-color); }
.type-unresolved_reference { color: var(--error-color, #db4437); }
.hyp { border-top: 1px solid var(--divider-color); padding: 12px 0; }
.hyp:first-of-type { border-top: 0; }
.hyp .title { font-weight: 500; overflow-wrap: anywhere; }
.hyp .sub { color: var(--secondary-text-color); font-size: 13px; margin-top: 2px; }
table.features { width: auto; margin-top: 8px; }
table.features th, table.features td { width: auto; text-align: center; padding: 4px 10px; font-variant-numeric: tabular-nums; }
.small-text { font-size: 12px; }
fieldset.plain { border: 0; padding: 0; margin: 0; }
fieldset.plain legend { font-size: 14px; color: var(--secondary-text-color); margin-bottom: 4px; }
button.danger { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
.hyp-strong { color: var(--error-color, #db4437); }
.hyp-possible { color: var(--warning-color, #ffa600); }
.hyp-none { color: var(--secondary-text-color); }
@media (max-width: 600px) {
  .grid2 { grid-template-columns: 1fr; }
  main { padding: 12px; }
  .metric { font-size: 32px; }
  dl { grid-template-columns: 1fr; gap: 2px; }
  dd { margin-bottom: 8px; }
}
`;
