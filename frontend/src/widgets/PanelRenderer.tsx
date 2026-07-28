// <PanelRenderer spec data /> — one dashboard panel: the panel cell chrome
// (byte-for-byte the .cell wrapper templates/dashboard.j2 emits — data-panel,
// the --w width custom property, the 13px title <h3>, the .body slot) wrapped
// around the registry widget for the resolved data's viz.
//
// `spec` is the RAW panel from the dashboard spec (id/title/width — the chrome
// reads these, exactly as dashboard.j2's `{% for p in panels %}` does). `data`
// is the resolved {viz,title,pin,data} (WS2-T2). `data === null` renders the
// chrome with an empty .body — the pre-fetch state dashboard.j2 shows before
// its per-panel fetch lands.
import { registry, hasError, ErrorTile, type PanelData } from "./registry";

export type PanelSpec = {
  id?: string;
  title?: string;
  width?: number;
};

export type ResolvedPanel = {
  viz: string;
  title: string;
  pin?: unknown;
  data: PanelData;
};

function Body({ resolved }: { resolved: ResolvedPanel }) {
  const { viz, title, data } = resolved;
  if (hasError(data)) return <ErrorTile message={data.error} />;
  const entry = registry[viz];
  if (!entry) return <ErrorTile message={`${title}: unsupported viz ${viz}`} />;
  const Widget = entry.component;
  return <Widget {...entry.adapt(data, title)} />;
}

export default function PanelRenderer({ spec, data }: { spec: PanelSpec; data: ResolvedPanel | null }) {
  const width = spec.width ?? 2;
  return (
    <div className="cell" data-panel={spec.id} style={{ "--w": width } as React.CSSProperties}>
      <h3 style={{ margin: "0 0 8px", fontSize: "13px" }}>{spec.title ?? ""}</h3>
      <div className="body">{data ? <Body resolved={data} /> : null}</div>
    </div>
  );
}
