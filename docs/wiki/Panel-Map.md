# Map

A dependency map of your installation: **"what depends on this?"** and **"what does this
depend on?"**.

## Where the links come from

Every link (edge) shows its **source** and **confidence**:

| Source | Example | Confidence |
|---|---|---|
| Registry | entity → device → integration entry; device → area | 1.0 |
| Static parse | imported automation → entity it reads, controls or calls | 1.0 (0.8 for a resolved blueprint input) |
| Manual | device → network host / endpoint you linked | 1.0 |

A reference to an entity that does not exist stays on the map as an **unresolved reference**
instead of disappearing (see DOC006).

## Using it

1. Search for an entity, device, integration or automation.
2. Choose the direction: **What depends on this** or **What this depends on**.
3. Choose the depth (1–5) and, if you want, include area grouping.
4. Click any object in the table to move to it.

Large answers are split into pages (up to 3000 links and 1000 objects per page); **Show
more** loads the next page. Cycles never cause an endless walk.

When the analysis of a source is incomplete (templates, unsupported fragments) the map says
so, instead of showing an empty or misleading list.

## History

The map is **versioned**. Every change to the graph creates a revision, and **Revision**
lets you look at an older one, with the names and references it had then (for example before
an entity was renamed). A banner marks the historical view. Unchanged rebuilds do not create
revisions; the last 200 revisions are kept.

The map is rebuilt automatically after registry changes, imports and network changes, and
fully once a day. Renaming an entity never creates a second object on the map.

Areas are shown as grouping only. They are never treated as a cause of failure, so two
unrelated devices in the same room are not linked.
