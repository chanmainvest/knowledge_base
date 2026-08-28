import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, Channel, Source } from "../api";
import { ColumnFilter, FilterOption } from "../components/ColumnFilter";
import { HitRateSpan, ScoreSpan, SortTh, useSort, useTitle } from "../components/ui";

export function ChannelsPage() {
  useTitle("Channels");
  const [sources, setSources] = useState<Source[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);

  type SortKey = "name" | "source" | "n_items" | "n_calls" | "n_scored" | "avg_score" | "hit_rate";
  const { sort, toggleSort, sortRows } = useSort<SortKey>("n_items", "desc",
    new Set<SortKey>(["name", "source"]));
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
  const channelOptions = useMemo<FilterOption<number>[]>(() =>
    channels
      .filter(c => sourceFilter === null || sourceFilter.has(c.source))
      .map(c => ({ value: c.id, label: c.name, count: c.n_items }))
      .sort((a, b) => a.label.localeCompare(b.label)),
    [channels, sourceFilter]);

  const visibleChannels = useMemo(() => {
    const filtered = channels.filter(c =>
      (sourceFilter === null || sourceFilter.has(c.source)) &&
      (channelFilter === null || channelFilter.has(c.id)));
    return sortRows(filtered, (c: Channel) => (c as any)[sort.key]);
  }, [channels, sourceFilter, channelFilter, sort, sortRows]);

  const hasFilters = sourceFilter !== null || channelFilter !== null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 text-sm text-mute">
          {hasFilters && (
            <button onClick={() => { setSourceFilter(null); setChannelFilter(null); }}
              className="text-accent hover:underline">Clear filters</button>
          )}
          <span>{visibleChannels.length} of {channels.length} channels</span>
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
              <SortTh label="Items" active={sort.key === "n_items"} dir={sort.dir}
                onSort={() => toggleSort("n_items")} align="right" />
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
            {visibleChannels.map(c => (
              <tr key={c.id} className="border-t border-border hover:bg-panel/30">
                <td className="p-2">
                  <Link to={`/search?channel_id=${c.id}`} className="text-accent hover:underline">{c.name}</Link>
                  <div className="text-xs text-mute">{c.handle}</div>
                </td>
                <td className="p-2 uppercase text-mute">{c.source}</td>
                <td className="p-2 text-right font-mono">{c.n_items}</td>
                <td className="p-2 text-right font-mono">
                  {c.n_calls > 0
                    ? <Link to={`/predictions?channel_id=${c.id}`} className="text-accent hover:underline">
                        {c.n_calls}
                      </Link>
                    : <span className="text-mute">{c.n_calls}</span>}
                </td>
                <td className="p-2 text-right font-mono">{c.n_scored}</td>
                <td className="p-2 text-right"><ScoreSpan v={c.avg_score} digits={3} /></td>
                <td className="p-2 text-right"><HitRateSpan v={c.hit_rate} /></td>
              </tr>
            ))}
            {visibleChannels.length === 0 && (
              <tr><td colSpan={7} className="p-4 text-center text-mute">No channels match the current filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
