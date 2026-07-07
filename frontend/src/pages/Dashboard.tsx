import { useEffect, useState } from "react";
import { api, Dashboard as DashboardData } from "../api";

function fmt(n: number): string { return n.toLocaleString(); }

function ts(v: string | null): string {
  if (!v) return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function StatCard({ label, value, tone }: { label: string; value: number; tone: "default" | "amber" | "red" }) {
  const color = tone === "red" ? "text-red-400" : tone === "amber" ? "text-amber-300" : "text-ink";
  return (
    <div className="bg-panel/60 border border-border rounded p-3">
      <div className="text-mute text-xs uppercase tracking-wide">{label}</div>
      <div className={"text-2xl font-semibold font-mono mt-1 " + color}>{fmt(value)}</div>
    </div>
  );
}

export function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.dashboard().then(setData).catch(e => setErr(String(e))); }, []);

  if (err) return <div className="text-red-400">{err}</div>;
  if (!data) return <div className="text-mute">Loading…</div>;

  const t = data.totals;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <StatCard label="Downloaded (tracked)" value={t.n_downloaded} tone="default" />
        <StatCard label="Pending download" value={t.n_pending_download} tone={t.n_pending_download > 0 ? "amber" : "default"} />
        <StatCard label="Ingested" value={t.n_ingested} tone="default" />
        <StatCard label="Extracted" value={t.n_extracted} tone="default" />
        <StatCard label="Pending extraction" value={t.n_extract_pending} tone={t.n_extract_pending > 0 ? "amber" : "default"} />
        <StatCard label="Extraction errors" value={t.n_extract_error} tone={t.n_extract_error > 0 ? "red" : "default"} />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm border border-border">
          <thead className="bg-panel/60 text-mute">
            <tr>
              <th className="text-left p-2">Source</th>
              <th className="text-left p-2">Kind</th>
              <th className="text-right p-2">Downloaded</th>
              <th className="text-right p-2">Pend. dl</th>
              <th className="text-right p-2">Total known</th>
              <th className="text-right p-2">Ingested</th>
              <th className="text-right p-2">Extracted</th>
              <th className="text-right p-2">Pending</th>
              <th className="text-right p-2">Errors</th>
              <th className="text-left p-2">Last scrape</th>
              <th className="text-left p-2">Last ingest</th>
              <th className="text-left p-2">Last extract</th>
            </tr>
          </thead>
          <tbody>
            {data.sources.map(s => (
              <tr key={s.code} className="border-t border-border hover:bg-panel/30">
                <td className="p-2 font-medium">{s.name}</td>
                <td className="p-2 text-mute">{s.kind}</td>
                <td className="p-2 text-right font-mono">{fmt(s.n_downloaded)}</td>
                <td className={"p-2 text-right font-mono " + (s.n_pending_download > 0 ? "text-amber-300" : "text-mute")}>
                  {fmt(s.n_pending_download)}
                </td>
                <td className="p-2 text-right font-mono text-mute">
                  {s.total_known != null ? fmt(s.total_known) : "—"}
                </td>
                <td className="p-2 text-right font-mono">{fmt(s.n_ingested)}</td>
                <td className="p-2 text-right font-mono">{fmt(s.n_extracted)}</td>
                <td className={"p-2 text-right font-mono " + (s.n_extract_pending > 0 ? "text-amber-300" : "text-mute")}>
                  {fmt(s.n_extract_pending)}
                </td>
                <td className={"p-2 text-right font-mono " + (s.n_extract_error > 0 ? "text-red-400" : "text-mute")}>
                  {fmt(s.n_extract_error)}
                </td>
                <td className="p-2 text-mute text-xs whitespace-nowrap">{ts(s.last_scrape_at)}</td>
                <td className="p-2 text-mute text-xs whitespace-nowrap">{ts(s.last_ingest_at)}</td>
                <td className="p-2 text-mute text-xs whitespace-nowrap">{ts(s.last_extract_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-mute">
        "Pend. dl" = items the scraper discovered but hasn't downloaded yet
        (a scrape that died halfway leaves rows here). Re-attempt them with
        <code className="font-mono"> kb scrape resume &lt;code&gt;</code>.
        "Total known" = upstream total where the source API exposes one
        (yahoohk, hkej, patreon); "—" = unknown. Downloaded is reconciled from
        the filesystem by <code className="font-mono">kb progress recompute</code>.
      </p>
    </div>
  );
}
