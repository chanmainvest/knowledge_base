import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, LB, LBRow, SpeakerLBRow, Source, ModelsLB } from "../api";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { ColumnFilter, FilterOption } from "../components/ColumnFilter";
import { ErrorBanner, HitRateSpan, ScoreSpan, SortTh, Spinner, useSort, useTitle } from "../components/ui";

const COLORS = ["#5cc8ff", "#ffd55c", "#ff7eb6", "#7ee787", "#a371f7",
                "#f97583", "#79b8ff", "#bfa3ff", "#ffa657", "#56d364"];

type Tab = "channels" | "speakers" | "models";

export function LeaderboardPage() {
  useTitle("Leaderboard");
  const [tab, setTab] = useState<Tab>("channels");

  const [data, setData] = useState<LB | null>(null);
  const [models, setModels] = useState<ModelsLB | null>(null);
  const [weeks, setWeeks] = useState(12);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const hasRange = !!dateFrom || !!dateTo;
  useEffect(() => {
    setData(null); setErr(null);
    api.leaderboard(weeks, dateFrom || undefined, dateTo || undefined)
      .then(setData)
      .catch(e => setErr(String(e?.message || e)));
  }, [weeks, dateFrom, dateTo]);

  useEffect(() => {
    if (tab === "models" && !models) {
      api.modelsLeaderboard().then(setModels).catch(() => setModels({ overall: [], by_channel: [] }));
    }
  }, [tab, models]);

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold">Leaderboard</h1>
          <div className="text-mute text-sm mt-0.5">
            Who called the market right — channels, speakers, and the LLMs reading them.
          </div>
        </div>
        <div className="flex gap-2 items-center text-sm">
          <span className="text-mute">Date range:</span>
          <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="bg-panel border border-border rounded px-2 py-1" aria-label="From date" />
          <span className="text-mute">–</span>
          <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="bg-panel border border-border rounded px-2 py-1" aria-label="To date" />
          {hasRange && (
            <button type="button" onClick={() => { setDateFrom(""); setDateTo(""); }}
              className="text-accent hover:underline text-xs">Clear</button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex rounded-md overflow-hidden border border-border">
          {([["channels", "Channels"], ["speakers", "Speakers"], ["models", "Models"]] as [Tab, string][])
            .map(([t, label]) => (
              <button key={t} onClick={() => setTab(t)}
                className={"px-3 py-1.5 text-sm " + (tab === t ? "bg-accent/15 text-accent" : "text-mute hover:text-ink bg-panel")}>
                {label}
              </button>
            ))}
        </div>
        <div className="flex gap-2 items-center ml-2">
          <label className="text-mute text-sm">Window:</label>
          {[4, 12, 26, 52].map(w => (
            <button key={w} onClick={() => setWeeks(w)} disabled={hasRange}
              title={hasRange ? "Clear the date range to use the weeks window" : ""}
              className={"px-2 py-1 rounded text-sm border border-border " +
                (weeks === w && !hasRange ? "bg-accent text-bg" : "bg-panel hover:bg-panel/70") +
                (hasRange ? " opacity-50 cursor-not-allowed" : "")}>
              {w}w
            </button>
          ))}
        </div>
      </div>

      <ErrorBanner error={err} />
      {!data && !err && <Spinner label="Loading leaderboard…" />}

      {data && tab === "channels" && <ChannelsTab data={data} />}
      {data && tab === "speakers" && <SpeakersTab data={data} />}
      {tab === "models" && <ModelsTab models={models} />}
    </div>
  );
}


// --- Channels tab ---------------------------------------------------------

function ChannelsTab({ data }: { data: LB }) {
  const [sources, setSources] = useState<Source[]>([]);
  useEffect(() => { api.sources().then(setSources).catch(() => {}); }, []);
  const sourceName = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sources) m.set(s.code, s.name);
    return m;
  }, [sources]);

  type SortKey = "name" | "source" | "n_calls" | "n_scored" | "avg_score" | "hit_rate";
  const { sort, toggleSort, sortRows } = useSort<SortKey>("avg_score", "desc",
    new Set<SortKey>(["name", "source"]));
  const [sourceFilter, setSourceFilter] = useState<Set<string> | null>(null);
  const [channelFilter, setChannelFilter] = useState<Set<number> | null>(null);
  const [query, setQuery] = useState("");

  const chartData = useMemo(() => {
    const byWeek: Record<string, any> = {};
    for (const r of data.weekly) {
      const wk = r.week_start || "";
      if (!byWeek[wk]) byWeek[wk] = { week_start: wk };
      byWeek[wk][r.name] = r.avg_score;
    }
    return Object.values(byWeek).sort((a: any, b: any) => a.week_start.localeCompare(b.week_start));
  }, [data]);

  const topNames = useMemo(() =>
    [...data.overall].slice(0, 10).map(r => r.name), [data]);

  const sourceOptions = useMemo<FilterOption<string>[]>(() => {
    const counts = new Map<string, number>();
    for (const r of data.overall) {
      if (channelFilter !== null && !channelFilter.has(r.channel_id)) continue;
      counts.set(r.source, (counts.get(r.source) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([value, count]) => ({ value, label: sourceName.get(value) ?? value, count }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [data, channelFilter, sourceName]);

  const channelOptions = useMemo<FilterOption<number>[]>(() =>
    (data.overall)
      .filter(r => sourceFilter === null || sourceFilter.has(r.source))
      .map(r => ({ value: r.channel_id, label: r.name, count: r.n_calls }))
      .sort((a, b) => a.label.localeCompare(b.label)),
    [data, sourceFilter]);

  const visibleOverall = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = data.overall.filter(r =>
      (sourceFilter === null || sourceFilter.has(r.source)) &&
      (channelFilter === null || channelFilter.has(r.channel_id)) &&
      (!q || r.name.toLowerCase().includes(q)));
    return sortRows(filtered, (r: LBRow) => (r as any)[sort.key]);
  }, [data, sourceFilter, channelFilter, query, sort, sortRows]);

  const hasFilters = sourceFilter !== null || channelFilter !== null;
  const hasWeekly = data.weekly.length > 0;

  return (
    <div className="space-y-6">
      <input value={query} onChange={e => setQuery(e.target.value)}
        placeholder="Filter by channel name…"
        className="w-full max-w-md bg-panel border border-border rounded px-3 py-2 outline-none focus:border-accent" />

      {hasWeekly && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Avg score by channel (weekly)</h2>
          <div className="h-72 bg-panel/40 border border-border rounded p-2">
            <ResponsiveContainer>
              <LineChart data={chartData}>
                <CartesianGrid stroke="#222a33" />
                <XAxis dataKey="week_start" stroke="#8a96a3" />
                <YAxis stroke="#8a96a3" domain={[-1, 1]} />
                <Tooltip contentStyle={{ background: "#13171c", border: "1px solid #222a33" }} />
                <Legend />
                {topNames.map((n, i) => (
                  <Line key={n} type="monotone" dataKey={n} stroke={COLORS[i % COLORS.length]}
                        dot={false} strokeWidth={2} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      <section>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-lg font-semibold">All-time by channel</h2>
          <div className="flex items-center gap-3 text-sm text-mute">
            {hasFilters && (
              <button onClick={() => { setSourceFilter(null); setChannelFilter(null); }}
                className="text-accent hover:underline">Clear filters</button>
            )}
            <span>{visibleOverall.length} of {data.overall.length} channels</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border border-border">
            <thead className="bg-panel/60 text-mute">
              <tr>
                <SortTh label="Channel" active={sort.key === "name"} dir={sort.dir}
                  onSort={() => toggleSort("name")}
                  filter={<ColumnFilter options={channelOptions} selected={channelFilter} onChange={setChannelFilter} />} />
                <SortTh label="Source" active={sort.key === "source"} dir={sort.dir}
                  onSort={() => toggleSort("source")}
                  filter={<ColumnFilter options={sourceOptions} selected={sourceFilter} onChange={setSourceFilter} />} />
                <SortTh label="Calls" active={sort.key === "n_calls"} dir={sort.dir}
                  onSort={() => toggleSort("n_calls")} align="right" />
                <SortTh label="Scored" active={sort.key === "n_scored"} dir={sort.dir}
                  onSort={() => toggleSort("n_scored")} align="right" />
                <SortTh label="Avg score" active={sort.key === "avg_score"} dir={sort.dir}
                  onSort={() => toggleSort("avg_score")} align="right" />
                <SortTh label="Hit rate" active={sort.key === "hit_rate"} dir={sort.dir}
                  onSort={() => toggleSort("hit_rate")} align="right" />
              </tr>
            </thead>
            <tbody>
              {visibleOverall.map(r => (
                <tr key={r.channel_id} className="border-t border-border hover:bg-panel/30">
                  <td className="p-2">
                    <Link to={`/search?channel_id=${r.channel_id}`} className="text-accent hover:underline">{r.name}</Link>
                    <div className="text-xs text-mute">{r.handle}</div>
                  </td>
                  <td className="p-2 uppercase text-mute">{r.source}</td>
                  <td className="p-2 text-right font-mono">{r.n_calls}</td>
                  <td className="p-2 text-right font-mono">{r.n_scored}</td>
                  <td className="p-2 text-right"><ScoreSpan v={r.avg_score} digits={3} /></td>
                  <td className="p-2 text-right"><HitRateSpan v={r.hit_rate} /></td>
                </tr>
              ))}
              {visibleOverall.length === 0 && (
                <tr><td colSpan={6} className="p-4 text-center text-mute">No channels match.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}


// --- Speakers tab -----------------------------------------------------------

function SpeakersTab({ data }: { data: LB }) {
  type SortKey = "speaker" | "n_calls" | "n_scored" | "avg_score" | "hit_rate" | "last_call_at";
  const { sort, toggleSort, sortRows } = useSort<SortKey>("n_scored", "desc",
    new Set<SortKey>(["speaker", "last_call_at"]));
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = data.speakers.filter(s =>
      !q || s.speaker.toLowerCase().includes(q) ||
      (s.main_channel_name || "").toLowerCase().includes(q));
    return sortRows(filtered, (s: SpeakerLBRow) => (s as any)[sort.key]);
  }, [data, query, sort, sortRows]);

  return (
    <div className="space-y-4">
      <input value={query} onChange={e => setQuery(e.target.value)}
        placeholder="Filter by speaker or channel…"
        className="w-full max-w-md bg-panel border border-border rounded px-3 py-2 outline-none focus:border-accent" />
      <div className="overflow-x-auto">
        <table className="w-full text-sm border border-border">
          <thead className="bg-panel/60 text-mute">
            <tr>
              <SortTh label="Speaker" active={sort.key === "speaker"} dir={sort.dir}
                onSort={() => toggleSort("speaker")} />
              <th className="text-left p-2">Main channel</th>
              <SortTh label="Calls" active={sort.key === "n_calls"} dir={sort.dir}
                onSort={() => toggleSort("n_calls")} align="right" />
              <SortTh label="Scored" active={sort.key === "n_scored"} dir={sort.dir}
                onSort={() => toggleSort("n_scored")} align="right" />
              <SortTh label="Avg score" active={sort.key === "avg_score"} dir={sort.dir}
                onSort={() => toggleSort("avg_score")} align="right" />
              <SortTh label="Hit rate" active={sort.key === "hit_rate"} dir={sort.dir}
                onSort={() => toggleSort("hit_rate")} align="right" />
              <SortTh label="Last call" active={sort.key === "last_call_at"} dir={sort.dir}
                onSort={() => toggleSort("last_call_at")} />
            </tr>
          </thead>
          <tbody>
            {visible.map(s => (
              <tr key={s.speaker} className="border-t border-border hover:bg-panel/30">
                <td className="p-2">
                  <Link to={`/predictions?speaker=${encodeURIComponent(s.speaker)}`}
                    className="text-accent hover:underline">{s.speaker}</Link>
                </td>
                <td className="p-2 text-mute">
                  {s.main_channel_id != null
                    ? <Link to={`/search?channel_id=${s.main_channel_id}`} className="hover:underline">
                        {s.main_channel_name}
                      </Link>
                    : "—"}
                </td>
                <td className="p-2 text-right font-mono">{s.n_calls}</td>
                <td className="p-2 text-right font-mono">{s.n_scored}</td>
                <td className="p-2 text-right"><ScoreSpan v={s.avg_score} digits={3} /></td>
                <td className="p-2 text-right"><HitRateSpan v={s.hit_rate} /></td>
                <td className="p-2 text-mute text-xs">{s.last_call_at?.slice(0, 10) ?? "—"}</td>
              </tr>
            ))}
            {visible.length === 0 && (
              <tr><td colSpan={7} className="p-4 text-center text-mute">
                No speakers match. Speakers come from the LLM extraction — run
                <code className="font-mono"> kb extract run</code> then
                <code className="font-mono"> kb leaderboard rebuild</code> if empty.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-mute">
        Speakers are the interviewees/authors the LLM attributes each call to, scored across
        every show they appear on. Click a name to see their individual calls.
      </p>
    </div>
  );
}


// --- Models tab --------------------------------------------------------------

function ModelsTab({ models }: { models: ModelsLB | null }) {
  if (!models) return <Spinner label="Loading model leaderboard…" />;
  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-lg font-semibold mb-2">Overall by provider/model</h2>
        <table className="w-full text-sm border border-border">
          <thead className="bg-panel/60 text-mute">
            <tr>
              <th className="text-left p-2">Provider</th>
              <th className="text-left p-2">Model</th>
              <th className="text-right p-2">Calls</th>
              <th className="text-right p-2">Scored</th>
              <th className="text-right p-2">Avg score</th>
              <th className="text-right p-2">Hit rate</th>
            </tr>
          </thead>
          <tbody>
            {models.overall.map(m => (
              <tr key={`${m.provider}/${m.model}`} className="border-t border-border hover:bg-panel/30">
                <td className="p-2 uppercase text-mute">{m.provider}</td>
                <td className="p-2 font-mono">{m.model || "(default)"}</td>
                <td className="p-2 text-right font-mono">{m.n_calls}</td>
                <td className="p-2 text-right font-mono">{m.n_scored}</td>
                <td className="p-2 text-right"><ScoreSpan v={m.avg_score} digits={3} /></td>
                <td className="p-2 text-right"><HitRateSpan v={m.hit_rate} /></td>
              </tr>
            ))}
            {models.overall.length === 0 && (
              <tr><td colSpan={6} className="p-4 text-center text-mute">
                No model comparison runs yet — try <code className="font-mono">kb extract compare &lt;item_id&gt;</code>.
              </td></tr>
            )}
          </tbody>
        </table>
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-2">By channel</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border border-border">
            <thead className="bg-panel/60 text-mute">
              <tr>
                <th className="text-left p-2">Channel</th>
                <th className="text-left p-2">Provider</th>
                <th className="text-left p-2">Model</th>
                <th className="text-right p-2">Calls</th>
                <th className="text-right p-2">Scored</th>
                <th className="text-right p-2">Avg score</th>
                <th className="text-right p-2">Hit rate</th>
              </tr>
            </thead>
            <tbody>
              {models.by_channel.map(m => (
                <tr key={`${m.channel_id}-${m.provider}-${m.model}`}
                    className="border-t border-border hover:bg-panel/30">
                  <td className="p-2">{m.channel_name}</td>
                  <td className="p-2 uppercase text-mute">{m.provider}</td>
                  <td className="p-2 font-mono">{m.model || "(default)"}</td>
                  <td className="p-2 text-right font-mono">{m.n_calls}</td>
                  <td className="p-2 text-right font-mono">{m.n_scored}</td>
                  <td className="p-2 text-right"><ScoreSpan v={m.avg_score} digits={3} /></td>
                  <td className="p-2 text-right"><HitRateSpan v={m.hit_rate} /></td>
                </tr>
              ))}
              {models.by_channel.length === 0 && (
                <tr><td colSpan={7} className="p-4 text-center text-mute">No per-channel model runs.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      <p className="text-xs text-mute">
        Which LLM extracts the most market-accurate calls from the same articles? Rows come from
        provider-comparison extraction runs (<code className="font-mono">kb extract compare</code>);
        unlike the channel/speaker tabs these intentionally include every run.
      </p>
    </div>
  );
}
