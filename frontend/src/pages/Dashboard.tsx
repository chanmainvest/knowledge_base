import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Dashboard as DashboardData, SearchResult, LB } from "../api";
import { Spinner, ErrorBanner, ScoreSpan, HitRateSpan, useTitle } from "../components/ui";

function fmt(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString();
}

function ts(v: string | null): string {
  if (!v) return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function StatCard({ label, value, tone, to }: {
  label: string; value: number | null; tone?: "amber" | "red"; to?: string;
}) {
  const color = tone === "red" ? "text-red-400" : tone === "amber" ? "text-amber-300" : "text-ink";
  const body = (
    <div className="bg-panel/60 border border-border rounded p-3 hover:border-accent/50 transition-colors">
      <div className="text-mute text-xs uppercase tracking-wide">{label}</div>
      <div className={"text-2xl font-semibold font-mono mt-1 " + color}>
        {value == null ? "—" : fmt(value)}
      </div>
    </div>
  );
  return to ? <Link to={to}>{body}</Link> : body;
}

export function DashboardPage() {
  useTitle("Dashboard");
  const [data, setData] = useState<DashboardData | null>(null);
  const [recent, setRecent] = useState<SearchResult | null>(null);
  const [lb, setLb] = useState<LB | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.dashboard(),
      api.items({ limit: 6 }),
      api.leaderboard(4),
    ]).then(([d, r, l]) => {
      if (cancelled) return;
      setData(d); setRecent(r); setLb(l);
    }).catch(e => { if (!cancelled) setErr(String(e?.message || e)); });
    return () => { cancelled = true; };
  }, []);

  if (err) return <ErrorBanner error={err} />;
  if (!data) return <Spinner label="Loading dashboard…" />;

  const t = data.totals;
  const topChannels = (lb?.overall ?? []).filter(r => r.n_scored > 0).slice(0, 5);
  const topSpeakers = (lb?.speakers ?? []).filter(r => r.n_scored > 0).slice(0, 5);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
        <StatCard label="Items" value={t.n_ingested} to="/search" />
        <StatCard label="Extracted" value={t.n_extracted} />
        <StatCard label="Pending extract" value={t.n_extract_pending}
          tone={t.n_extract_pending > 0 ? "amber" : undefined} />
        <StatCard label="Errors" value={t.n_extract_error}
          tone={t.n_extract_error > 0 ? "red" : undefined} />
        <StatCard label="Predictions" value={t.n_predictions} to="/predictions" />
        <StatCard label="Scored calls" value={t.n_scored} to="/predictions?scored=true" />
        <StatCard label="Speakers" value={t.n_speakers} to="/leaderboard" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Latest</h2>
            <Link to="/search" className="text-accent text-sm hover:underline">Browse all →</Link>
          </div>
          <ul className="space-y-2">
            {(recent?.items ?? []).map(h => (
              <li key={h.id} className="border border-border rounded p-3 bg-panel/40 hover:border-accent/40">
                <div className="text-xs text-mute flex gap-2 mb-1 flex-wrap">
                  <span className="uppercase">{h.source}</span>
                  {h.channel_name && <span>· {h.channel_name}</span>}
                  {h.published_at
                    ? <span>· {h.published_at.slice(0, 10)}</span>
                    : <span>· undated</span>}
                  {h.has_predictions && <span className="text-accent">· has predictions</span>}
                </div>
                <Link to={`/items/${h.id}`} className="text-base hover:underline">{h.title}</Link>
              </li>
            ))}
            {recent && recent.items.length === 0 && (
              <li className="text-mute text-sm">Nothing ingested yet.</li>
            )}
          </ul>
        </section>

        <section className="space-y-5">
          <div>
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-lg font-semibold">Top speakers</h2>
              <Link to="/leaderboard" className="text-accent text-sm hover:underline">All →</Link>
            </div>
            <table className="w-full text-sm">
              <tbody>
                {topSpeakers.map(s => (
                  <tr key={s.speaker} className="border-b border-border/60">
                    <td className="py-1.5">
                      <Link to={`/predictions?speaker=${encodeURIComponent(s.speaker)}`}
                        className="hover:underline">{s.speaker}</Link>
                      {s.main_channel_name &&
                        <div className="text-xs text-mute">{s.main_channel_name}</div>}
                    </td>
                    <td className="py-1.5 text-right text-mute font-mono text-xs">
                      {s.n_scored}/{s.n_calls}
                    </td>
                    <td className="py-1.5 text-right"><ScoreSpan v={s.avg_score} digits={2} /></td>
                    <td className="py-1.5 text-right"><HitRateSpan v={s.hit_rate} /></td>
                  </tr>
                ))}
                {topSpeakers.length === 0 && (
                  <tr><td className="text-mute py-2 text-sm">
                    No scored calls yet — run <code className="font-mono">kb leaderboard rebuild</code>.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-lg font-semibold">Top channels</h2>
              <Link to="/leaderboard" className="text-accent text-sm hover:underline">All →</Link>
            </div>
            <table className="w-full text-sm">
              <tbody>
                {topChannels.map(c => (
                  <tr key={c.channel_id} className="border-b border-border/60">
                    <td className="py-1.5">
                      <Link to={`/search?channel_id=${c.channel_id}`} className="hover:underline">
                        {c.name}
                      </Link>
                    </td>
                    <td className="py-1.5 text-right text-mute font-mono text-xs">
                      {c.n_scored}/{c.n_calls}
                    </td>
                    <td className="py-1.5 text-right"><ScoreSpan v={c.avg_score} digits={2} /></td>
                    <td className="py-1.5 text-right"><HitRateSpan v={c.hit_rate} /></td>
                  </tr>
                ))}
                {topChannels.length === 0 && (
                  <tr><td className="text-mute py-2 text-sm">No scored channels yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <details className="border border-border rounded bg-panel/30" open={false}>
        <summary className="p-3 cursor-pointer text-mute text-sm select-none hover:text-ink">
          Pipeline health (per source)
        </summary>
        <div className="overflow-x-auto px-3 pb-3">
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
          <p className="text-xs text-mute mt-2">
            "Pend. dl" = items the scraper discovered but hasn't downloaded yet (re-attempt with
            <code className="font-mono"> kb scrape resume &lt;code&gt;</code>).
            "Total known" = upstream total where the source API exposes one.
          </p>
        </div>
      </details>
    </div>
  );
}
