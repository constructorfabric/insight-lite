// /data-health — the trust surface + dataset stats, migrated to React (Manage
// migration). Fetches GET /api/manage/data-health.json (= datahealth.health_json)
// and reproduces datahealth.render_page()'s markup + classes exactly (see
// ../styles/datahealth.css) so the screenshot-diff gate sees no pixel difference.
// Static, read-only page.
//
// SSR-safe: no top-level window/document access — only inside the effect.
import { useEffect, useState } from "react";

type Tile = {
  cls: string;
  val: string;
  label: string;
  fix: string;
  href: string | null;
  done: boolean;
};
type Stat = { label: string; value: string; sub: string };
type Health = { riskLine: string; tiles: Tile[]; stats: Stat[] };

// Mirrors render_page()'s _tile(): the fix line's class depends on done/href.
function TileInner({ t }: { t: Tile }) {
  const fixcls = t.done ? "qfix done" : t.href ? "qfix" : "qfix muted";
  return (
    <>
      <div className="qv">{t.val}</div>
      <div className="ql">{t.label}</div>
      <div className={fixcls}>{t.fix}</div>
    </>
  );
}

export default function DataHealth() {
  const [data, setData] = useState<Health | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/manage/data-health.json")
      .then((res) => res.json())
      .then((d) => {
        if (!cancelled && d && d.ok) setData(d);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const tiles = data?.tiles ?? [];
  const stats = data?.stats ?? [];

  return (
    <>
      <h1>Data health</h1>
      <p className="sub">
        Review before sharing — the trust surface and the shape of the collected data.
      </p>
      <p className="lead">{data?.riskLine ?? ""}</p>
      <div className="sec-h">Trust surface</div>
      <div className="quality">
        {tiles.map((t, i) =>
          t.href ? (
            <a key={i} className={"qitem " + t.cls} href={t.href}>
              <TileInner t={t} />
            </a>
          ) : (
            <div key={i} className={"qitem " + t.cls}>
              <TileInner t={t} />
            </div>
          ),
        )}
      </div>
      <div className="sec-h">Data at a glance</div>
      <div className="statgrid">
        {stats.map((s, i) => (
          <div className="stat" key={i}>
            <div className="sv num">{s.value}</div>
            <div className="sl">{s.label}</div>
            <div className="sub">{s.sub}</div>
          </div>
        ))}
      </div>
    </>
  );
}
