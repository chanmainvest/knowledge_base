import { useEffect, useMemo, useState } from "react";
import { api, LB, LBRow, Source } from "../api";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { ColumnFilter, FilterOption } from "../components/ColumnFilter";

const COLORS = ["#5cc8ff", "#ffd55c", "#ff7eb6", "#7ee787", "#a371f7",
                "#f97583", "#79b8ff", "#bfa3ff", "#ffa657", "#56d364"];

// Sortable columns of the overall table. Text keys default to ascending on
// first click; numeric keys default to descending (mirrors Channels page).
type SortKey = "name" | "source" | "n_calls" | "n_scored" | "avg_score" | "hit_rate";
type SortDir = "asc" | "desc";
const TEXT_KEYS = new Set<SortKey>(["name", "source"]);

// Nulls (e.g. avg_score/hit_rate before any prediction is scored) always
// sort last, regardless of direction. Same convention as Channels page.
function compareRows(a: LBRow, b: LBRow, key: SortKey, dir: SortDir): number {
  const av = (a as any)[key];
  const bv = (b as any)[key];
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  const sign = dir === "asc" ? 1 : -1;
  return typeof av === "string" ? sign * av.localeCompare(bv) : sign * (av - bv);
}

export function LeaderboardPage() {
  const [data, setData] = useState<LB | null>(null);
  const [weeks, setWeeks] = useState(12);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const [sources, setSources] = useState<Source[]>([]);
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "avg_score", dir: "desc" });
  const [sourceFilter, setSourceFilter] = useState<Set<string> | null>(null);
  const [channelFilter, setChannelFilter] = useState<Set<number> | null>(null);
  // Free-text channel-name filter (client-side contains match, case-insensitive).
  const [query, setQuery] = useState("");

  // An explicit date range overrides the weeks window (the backend applies
  // it to both the weekly series and the overall aggregate). Either control
  // is sent; the weeks buttons are disabled while a range is active so the
  // two can't fight each other.
  const hasRange = !!dateFrom || !!dateTo;
  useEffect(() => {
    api.leaderboard(weeks, dateFrom || undefined, dateTo || undefined).then(setData);
  }, [weeks, dateFrom, dateTo]);

  useEffect(() => { api.sources().then(setSources).catch(() => {}); }, []);

  const sourceName = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sources) m.set(s.code, s.name);
    return m;
  }, [sources]);

  const chartData = useMemo(() => {
    if (!data) return [] as any[];
    const byWeek: Record<string, any> = {};
    for (const r of data.weekly) {
      const wk = r.week_start || "";
      if (!byWeek[wk]) byWeek[wk] = { week_start: wk };
      byWeek[wk][r.name] = r.avg_score;
    }
    return Object.values(byWeek).sort((a: any, b: any) =>
      a.week_start.localeCompare(b.week_start));
  }, [data]);

  const topNames = useMemo(() => {
    if (!data) return [] as string[];
    return [...data.overall].slice(0, 10).map(r => r.name);
  }, [data]);

  // Excel-style filter options for the overall table. Source options narrow
  // with the channel filter and vice-versa exactly like the Channels page:
  // each column's dropdown lists rows still visible after the *other* filter.
  const sourceOptions = useMemo<FilterOption<string>[]>(() => {
    const counts = new Map<string, number>();
    for (const r of data?.overall ?? []) {
      if (channelFilter !== null && !channelFilter.has(r.channel_id)) continue;
      counts.set(r.source, (counts.get(r.source) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([value, count]) => ({ value, label: sourceName.get(value) ?? value, count }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [data, channelFilter, sourceName]);

  const channelOptions = useMemo<FilterOption<number>[]>(() => {
    return (data?.overall ?? [])
      .filter(r => sourceFilter === null || sourceFilter.has(r.source))
      .map(r => ({ value: r.channel_id, label: r.name, count: r.n_calls }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [data, sourceFilter]);

  const visibleOverall = useMemo(() => {
    if (!data) return [] as LBRow[];
    const q = query.trim().toLowerCase();
    const filtered = data.overall.filter(r =>
      (sourceFilter === null || sourceFilter.has(r.source)) &&
      (channelFilter === null || channelFilter.has(r.channel_id)) &&
      (!q || r.name.toLowerCase().includes(q)));
    return [...filtered].sort((a, b) => compareRows(a, b, sort.key, sort.dir));
  }, [data, sourceFilter, channelFilter, query, sort]);

  function toggleSort(key: SortKey) {
    setSort(s => s.key === key
      ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
      : { key, dir: TEXT_KEYS.has(key) ? "asc" : "desc" });
  }

  function clearDates() { setDateFrom(""); setDateTo(""); }

  const hasFilters = sourceFilter !== null || channelFilter !== null;
  function clearFilters() { setSourceFilter(null); setChannelFilter(null); }

  if (!data) return <div className="text-mute">Loading…</div>;

  return (
    <div className="space-y-6">
      {/* Search box + date range, side by side. The text query filters
          channel names client-side; the date range overrides the weeks
          window and is applied to both the weekly series and the overall
          aggregate by the backend. */}
      <div className="flex gap-2 items-center flex-wrap">
        <input value={query} onChange={e => setQuery(e.target.value)}
          placeholder="Filter by channel name…"
          className="flex-1 min-w-[12rem] bg-panel border border-border rounded px-3 py-2 outline-none focus:border-accent" />
        <div className="flex gap-2 items-center text-sm">
          <span className="text-mute">Date range:</span>
          <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="bg-panel border border-border rounded px-2 py-1" aria-label="From date" />
          <span className="text-mute">–</span>
          <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="bg-panel border border-border rounded px-2 py-1" aria-label="To date" />
          {hasRange && (
            <button type="button" onClick={clearDates}
              className="text-accent hover:underline text-xs">
              Clear
            </button>
          )}
        </div>
      </div>

      <div className="flex gap-2 items-center">
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

      <section>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-lg font-semibold">
            {hasRange ? "Leaderboard (selected range)" : "All-time leaderboard"}
          </h2>
          <div className="flex items-center gap-3 text-sm text-mute">
            {hasFilters && (
              <button onClick={clearFilters} className="text-accent hover:underline">Clear filters</button>
            )}
            <span>{visibleOverall.length} of {data.overall.length} channels</span>
          </div>
        </div>
        <table className="w-full text-sm border border-border">
          <thead className="bg-panel/60 text-mute">
            <tr>
              <Th label="Channel" sortKey="name" sort={sort} onSort={toggleSort}
                filter={<ColumnFilter options={channelOptions} selected={channelFilter} onChange={setChannelFilter} />} />
              <Th label="Source" sortKey="source" sort={sort} onSort={toggleSort}
                filter={<ColumnFilter options={sourceOptions} selected={sourceFilter} onChange={setSourceFilter} />} />
              <Th label="Calls" sortKey="n_calls" sort={sort} onSort={toggleSort} align="right" />
              <Th label="Scored" sortKey="n_scored" sort={sort} onSort={toggleSort} align="right" />
              <Th label="Avg score" sortKey="avg_score" sort={sort} onSort={toggleSort} align="right" />
              <Th label="Hit rate" sortKey="hit_rate" sort={sort} onSort={toggleSort} align="right" />
            </tr>
          </thead>
          <tbody>
            {visibleOverall.map(r => (
              <tr key={r.channel_id} className="border-t border-border hover:bg-panel/30">
                <td className="p-2">{r.name}</td>
                <td className="p-2 uppercase text-mute">{r.source}</td>
                <td className="p-2 text-right font-mono">{r.n_calls}</td>
                <td className="p-2 text-right font-mono">{r.n_scored}</td>
                <td className={"p-2 text-right font-mono " + (
                  r.avg_score == null ? "text-mute" :
                  r.avg_score > 0 ? "text-green-400" :
                  r.avg_score < 0 ? "text-red-400" : "")}>
                  {r.avg_score == null ? "—" : r.avg_score.toFixed(3)}
                </td>
                <td className="p-2 text-right font-mono">
                  {r.hit_rate == null ? "—" : (r.hit_rate * 100).toFixed(0) + "%"}
                </td>
              </tr>
            ))}
            {visibleOverall.length === 0 && (
              <tr><td colSpan={6} className="p-4 text-center text-mute">No channels match the current filters.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}

interface ThProps {
  label: string;
  sortKey: SortKey;
  sort: { key: SortKey; dir: SortDir };
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
  filter?: React.ReactNode;
}

function Th({ label, sortKey, sort, onSort, align = "left", filter }: ThProps) {
  const active = sort.key === sortKey;
  return (
    <th className={"p-2 select-none whitespace-nowrap " + (align === "right" ? "text-right" : "text-left")}>
      <button type="button" onClick={() => onSort(sortKey)}
        className="inline-flex items-center gap-1 hover:text-ink">
        <span>{label}</span>
        <span className="text-[9px] text-accent w-2.5 inline-block">
          {active ? (sort.dir === "asc" ? "▲" : "▼") : ""}
        </span>
      </button>
      {filter}
    </th>
  );
}