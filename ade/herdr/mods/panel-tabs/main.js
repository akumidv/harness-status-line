// The fixed switch above the right panel: Changes | Files (the stack `panel-body`).
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { run, theme, layout } from "herdr";

function find(node, id) {
  if (node?.id === id) return node;
  for (const c of node?.children ?? []) { const f = find(c, id); if (f) return f; }
  return null;
}

const ITEMS = [["changes", "Changes", "mods/git-panel/changes.svg"], ["files", "Files", "mods/file-panel/file.svg"]];

export default class PanelTabs extends View {
  render() {
    const t = theme(), body = find(layout(), "panel-body"), active = body?.active ?? "changes";
    return h_flex().id("panel-tabs").size_full().px(6).gap(3).items_center().text_size(12)
      .children(ITEMS.map(([id, label, icon]) => h_flex().id(`pt-${id}`).h(26).px(10).gap(6).flex_none().items_center().rounded(6).cursor_pointer()
        .bg(active === id ? t.raised : t.chrome).text_color(active === id ? t.foreground : t.muted)
        .border(1).border_color(active === id ? t.border : t.chrome).hover((s) => s.bg(t.hover))
        .on_click(() => run("layout.select", { stack: "panel-body", child: id }))
        .child(svg(icon).size(14)).child(label)));
  }
}
