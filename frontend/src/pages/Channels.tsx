import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, Channel, Source } from "../api";
import { ColumnFilter, FilterOption } from "../components/ColumnFilter";

type SortKey = "name" | "source" | "n_items" | "n_calls" | "n_scored" | "avg_score" | "hit_rate";
type SortDir = "asc" | "desc";
const TEXT_KEYS = new Set<SortKey>(["name", "source"]);

// Nulls (e.g. avg_score/hit_rate before any prediction is scored) always
// sort last, regardless of direction.
function compareChannels(a: Channel, b: Channel, key: SortKey, dir: SortDir): number {
  const av = (a as any)[key];
  const bv = (b as any)[key];
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  const sign = dir === "asc" ? 1 : -1;
  return typeof av === "string" ? sign * av.localeCompare(bv) : sign * (av - bv);
}

export function ChannelsPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);

  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "n_items", dir: "desc" });
  const [sourceFilter, setSourceFilter] = useState<Set<string> | null>(null);
  const [channelFilter, setChannelFilter] = useState<Set<number> | null>(null);

  useEffect(() => { api.sources().then(setSources).catch(() => {}); }, []);
  // Load every channel once; sorting/filtering below is all client-side so
  // the Excel-style column filters can narrow/re-narrow without refetching.
  useEffect(() => { api.channels().then(setChannels).catch(() => setChannels([])); }, []);

  const sourceName = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sources) m.set(s.code, s.name);
    return m;
  }, [sources]);

  const sourceOptions = useMemo<FilterOption<string>[]>(() => {
    const counts = new Map<string, number>();
    for (const c of channels) counts.set(c.source, (counts.get(c.source) ?? 0) + 1);
    return [...counts.entries()]
      .map(([value, count]) => ({ value, label: sourceName.get(value) ?? value, count }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [channels, sourceName]);

  // Channel filter options narrow with the source filter (the "outer"
  // filter) but never with the channel filter itself, so unchecked rows
  // stay listed in the dropdown for the user to fine-tune.
  const channelOptions = useMemo<FilterOption<number>[]>(() => {
    return channels
      .filter(c => sourceFilter === null || sourceFilter.has(c.source))
      .map(c => ({ value: c.id, label: c.name, count: c.n_items }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [channels, sourceFilter]);

  const visibleChannels = useMemo(() => {
    const filtered = channels.filter(c =>
      (sourceFilter === null || sourceFilter.has(c.source)) &&
      (channelFilter === null || channelFilter.has(c.id)));
    return [...filtered].sort((a, b) => compareChannels(a, b, sort.key, sort.dir));
  }, [channels, sourceFilter, channelFilter, sort]);

  function toggleSort(key: SortKey) {
    setSort(s => s.key === key
      ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
      : { key, dir: TEXT_KEYS.has(key) ? "asc" : "desc" });
  }

  const hasFilters = sourceFilter !== null || channelFilter !== null;
  function clearFilters() { setSourceFilter(null); setChannelFilter(null); }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Channels</h2>
        <div className="flex items-center gap-3 text-sm text-mute">
          {hasFilters && (
            <button onClick={clearFilters} className="text-accent hover:underline">Clear filters</button>
          )}
          <span>{visibleChannels.length} of {channels.length} channels</span>
        </div>
      </div>
      <table className="w-full text-sm border border-border">
        <thead className="bg-panel/60 text-mute">
          <tr>
            <Th label="Channel" sortKey="name" sort={sort} onSort={toggleSort}
              filter={<ColumnFilter options={channelOptions} selected={channelFilter} onChange={setChannelFilter} />} />
            <Th label="Source" sortKey="source" sort={sort} onSort={toggleSort}
              filter={<ColumnFilter options={sourceOptions} selected={sourceFilter} onChange={setSourceFilter} />} />
            <Th label="Items" sortKey="n_items" sort={sort} onSort={toggleSort} align="right" />
            <Th label="Calls" sortKey="n_calls" sort={sort} onSort={toggleSort} align="right" />
            <Th label="Scored" sortKey="n_scored" sort={sort} onSort={toggleSort} align="right" />
            <Th label="Avg score" sortKey="avg_score" sort={sort} onSort={toggleSort} align="right" />
            <Th label="Hit rate" sortKey="hit_rate" sort={sort} onSort={toggleSort} align="right" />
          </tr>
        </thead>
        <tbody>
          {visibleChannels.map(c => (
            <tr key={c.id} className="border-t border-border hover:bg-panel/30">
              <td className="p-2">
                <Link to={`/search?channel_id=${c.id}`} className="text-accent">{c.name}</Link>
                <div className="text-xs text-mute">{c.handle}</div>
              </td>
              <td className="p-2 uppercase text-mute">{c.source}</td>
              <td className="p-2 text-right font-mono">{c.n_items}</td>
              <td className="p-2 text-right font-mono">{c.n_calls}</td>
              <td className="p-2 text-right font-mono">{c.n_scored}</td>
              <td className={"p-2 text-right font-mono " + (
                c.avg_score == null ? "text-mute" :
                c.avg_score > 0 ? "text-green-400" :
                c.avg_score < 0 ? "text-red-400" : "")}>
                {c.avg_score == null ? "—" : c.avg_score.toFixed(3)}
              </td>
              <td className="p-2 text-right font-mono">
                {c.hit_rate == null ? "—" : (c.hit_rate * 100).toFixed(0) + "%"}
              </td>
            </tr>
          ))}
          {visibleChannels.length === 0 && (
            <tr><td colSpan={7} className="p-4 text-center text-mute">No channels match the current filters.</td></tr>
          )}
        </tbody>
      </table>
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
