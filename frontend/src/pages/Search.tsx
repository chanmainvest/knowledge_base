import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, Source, Channel, SearchHit } from "../api";

const PAGE_SIZES = [25, 50, 100, 200];

export function SearchPage() {
  const [urlParams] = useSearchParams();

  const [q, setQ] = useState("");
  const [submittedQ, setSubmittedQ] = useState("");

  const [sources, setSources] = useState<Source[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [selectedChannels, setSelectedChannels] = useState<number[]>([]);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [hasPredictions, setHasPredictions] = useState<"" | "true" | "false">("");

  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  const [items, setItems] = useState<SearchHit[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Preselect a channel when arriving from the Channels page
  // (`/search?channel_id=<id>`).
  useEffect(() => {
    const cid = urlParams.get("channel_id");
    if (cid) setSelectedChannels([Number(cid)]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { api.sources().then(setSources).catch(() => {}); }, []);

  // Channels list narrows to the selected sources (multi-select); reload
  // whenever the source selection changes and drop any selected channel that
  // no longer applies.
  useEffect(() => {
    api.channels(selectedSources.length ? selectedSources : undefined)
      .then(chs => {
        setChannels(chs);
        setSelectedChannels(sel => sel.filter(id => chs.some(c => c.id === id)));
      })
      .catch(() => setChannels([]));
  }, [selectedSources]);

  // Default view: latest posts (q omitted). Re-runs whenever the query or
  // any filter/pagination control changes.
  useEffect(() => {
    let cancelled = false;
    setBusy(true); setErr(null);
    api.search({
      q: submittedQ || undefined,
      source: selectedSources.length ? selectedSources : undefined,
      channel_id: selectedChannels.length ? selectedChannels : undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      has_predictions: hasPredictions === "" ? undefined : hasPredictions === "true",
      limit: pageSize,
      offset: (page - 1) * pageSize,
    }).then(res => {
      if (cancelled) return;
      setItems(res.items);
      setTotal(res.total);
    }).catch(ex => {
      if (!cancelled) setErr(String(ex?.message || ex));
    }).finally(() => {
      if (!cancelled) setBusy(false);
    });
    return () => { cancelled = true; };
  }, [submittedQ, selectedSources, selectedChannels, dateFrom, dateTo, hasPredictions, pageSize, page]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const channelsBySource = useMemo(() => {
    const m = new Map<string, Channel[]>();
    for (const c of channels) {
      if (!m.has(c.source)) m.set(c.source, []);
      m.get(c.source)!.push(c);
    }
    return m;
  }, [channels]);

  function submitQuery(e?: React.FormEvent) {
    e?.preventDefault();
    setPage(1);
    setSubmittedQ(q.trim());
  }
  function clearQuery() {
    setQ(""); setSubmittedQ(""); setPage(1);
  }
  function toggleSource(code: string) {
    setPage(1);
    setSelectedSources(sel => sel.includes(code) ? sel.filter(s => s !== code) : [...sel, code]);
  }
  function toggleChannel(id: number) {
    setPage(1);
    setSelectedChannels(sel => sel.includes(id) ? sel.filter(c => c !== id) : [...sel, id]);
  }
  function clearFilters() {
    setPage(1);
    setSelectedSources([]);
    setSelectedChannels([]);
    setDateFrom("");
    setDateTo("");
    setHasPredictions("");
  }
  const hasFilters = selectedSources.length > 0 || selectedChannels.length > 0
    || !!dateFrom || !!dateTo || hasPredictions !== "";

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-6">
      <div className="space-y-4 min-w-0">
        <form onSubmit={submitQuery} className="flex gap-2">
          <input value={q} onChange={e => setQ(e.target.value)}
            placeholder="Search transcripts and articles… (leave empty to browse latest)"
            className="flex-1 min-w-0 bg-panel border border-border rounded px-3 py-2 outline-none focus:border-accent" />
          <button disabled={busy} className="bg-accent text-bg font-medium rounded px-4 py-2 shrink-0">
            {busy ? "…" : "Search"}
          </button>
          {submittedQ && (
            <button type="button" onClick={clearQuery}
              className="border border-border rounded px-3 py-2 text-mute hover:text-ink shrink-0">
              Clear
            </button>
          )}
        </form>

        {err && <div className="text-red-400 text-sm">{err}</div>}

        <div className="flex items-center justify-between text-sm text-mute gap-2 flex-wrap">
          <span>
            {busy ? "Loading…" : total === 0 ? "No results." :
              `Showing ${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}`}
          </span>
          <label className="flex items-center gap-2">
            Rows per page
            <select value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}
              className="bg-panel border border-border rounded px-2 py-1">
              {PAGE_SIZES.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </div>

        <ul className="space-y-3">
          {items.map(h => (
            <li key={h.id} className="border border-border rounded p-3 bg-panel/40">
              <div className="text-xs text-mute flex gap-2 mb-1">
                <span className="uppercase">{h.source}</span>
                {h.channel_name && <span>· {h.channel_name}</span>}
                {h.published_at && <span>· {h.published_at.slice(0, 10)}</span>}
                {h.has_predictions && (
                  <span className="text-accent normal-case">· has predictions</span>
                )}
              </div>
              <Link to={`/items/${h.id}`} className="text-lg text-accent hover:underline">
                {h.title}
              </Link>
              {h.snippet ? (
                <div className="snippet text-sm mt-1"
                     dangerouslySetInnerHTML={{ __html: h.snippet }} />
              ) : h.summary ? (
                <div className="text-sm mt-1 text-mute line-clamp-2">{h.summary}</div>
              ) : null}
            </li>
          ))}
        </ul>

        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-3 text-sm pt-2">
            <button disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}
              className="border border-border rounded px-3 py-1 disabled:opacity-40">
              Prev
            </button>
            <span className="text-mute">Page {page} of {totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              className="border border-border rounded px-3 py-1 disabled:opacity-40">
              Next
            </button>
          </div>
        )}
      </div>

      <aside className="space-y-5 text-sm">
        <div className="flex items-center justify-between">
          <h3 className="text-mute text-xs uppercase tracking-wide">Filters</h3>
          {hasFilters && (
            <button onClick={clearFilters} className="text-xs text-accent hover:underline">
              Clear all
            </button>
          )}
        </div>

        <section>
          <h4 className="text-mute text-xs uppercase tracking-wide mb-2">Date range</h4>
          <div className="flex flex-col gap-2">
            <label className="flex items-center gap-2">
              <span className="text-mute text-xs w-8">From</span>
              <input type="date" value={dateFrom}
                onChange={e => { setDateFrom(e.target.value); setPage(1); }}
                className="flex-1 bg-panel border border-border rounded px-2 py-1" />
            </label>
            <label className="flex items-center gap-2">
              <span className="text-mute text-xs w-8">To</span>
              <input type="date" value={dateTo}
                onChange={e => { setDateTo(e.target.value); setPage(1); }}
                className="flex-1 bg-panel border border-border rounded px-2 py-1" />
            </label>
          </div>
        </section>

        <section>
          <h4 className="text-mute text-xs uppercase tracking-wide mb-2">Prediction extraction</h4>
          <select value={hasPredictions}
            onChange={e => { setHasPredictions(e.target.value as "" | "true" | "false"); setPage(1); }}
            className="w-full bg-panel border border-border rounded px-2 py-1">
            <option value="">All items</option>
            <option value="true">With predictions</option>
            <option value="false">Without predictions</option>
          </select>
        </section>

        <section>
          <h4 className="text-mute text-xs uppercase tracking-wide mb-2">
            Sources {selectedSources.length > 0 && `(${selectedSources.length})`}
          </h4>
          <div className="space-y-1 max-h-56 overflow-y-auto pr-1 border border-border rounded p-2">
            {sources.map(s => (
              <label key={s.id} className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={selectedSources.includes(s.code)}
                  onChange={() => toggleSource(s.code)} />
                <span className="flex-1 truncate">{s.name}</span>
                <span className="text-mute text-xs">{s.n_items}</span>
              </label>
            ))}
          </div>
        </section>

        <section>
          <h4 className="text-mute text-xs uppercase tracking-wide mb-2">
            Channels {selectedChannels.length > 0 && `(${selectedChannels.length})`}
          </h4>
          <div className="space-y-2 max-h-72 overflow-y-auto pr-1 border border-border rounded p-2">
            {[...channelsBySource.entries()].map(([src, chs]) => (
              <div key={src}>
                <div className="text-mute text-[10px] uppercase mb-1">{src}</div>
                {chs.map(c => (
                  <label key={c.id} className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={selectedChannels.includes(c.id)}
                      onChange={() => toggleChannel(c.id)} />
                    <span className="flex-1 truncate">{c.name}</span>
                    <span className="text-mute text-xs">{c.n_items}</span>
                  </label>
                ))}
              </div>
            ))}
            {channels.length === 0 && <div className="text-mute text-xs">No channels.</div>}
          </div>
        </section>
      </aside>
    </div>
  );
}
