import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, Source, Channel, SearchHit } from "../api";
import { ErrorBanner, Spinner, useTitle } from "../components/ui";

const PAGE_SIZES = [25, 50, 100, 200];

// All filter state lives in the URL (via useSearchParams), so filtered views
// are shareable, the browser back/forward buttons step through filter
// changes, and inbound deep links (?channel_id=… from the Channels page)
// just work. The text input is the only local state — it commits to the URL
// on submit, like a normal search box.
export function SearchPage() {
  useTitle("Search");
  const [params, setParams] = useSearchParams();

  const submittedQ = params.get("q") ?? "";
  const selectedSources = params.getAll("source");
  const selectedChannels = params.getAll("channel_id").map(Number).filter(n => !isNaN(n));
  const dateFrom = params.get("date_from") ?? "";
  const dateTo = params.get("date_to") ?? "";
  const hasPredictions = params.get("has_predictions") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const pageSize = Math.max(1, Number(params.get("page_size")) || 25);

  const [qInput, setQInput] = useState(submittedQ);
  // Keep the input in sync when the URL changes elsewhere (back button).
  useEffect(() => { setQInput(submittedQ); }, [submittedQ]);

  const [sources, setSources] = useState<Source[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [items, setItems] = useState<SearchHit[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.sources().then(setSources).catch(() => {}); }, []);

  useEffect(() => {
    api.channels(selectedSources.length ? selectedSources : undefined)
      .then(chs => {
        setChannels(chs);
        // Drop selected channels that don't belong to the (remaining)
        // sources, e.g. after a source checkbox was turned off.
        const ids = new Set(chs.map(c => c.id));
        const stale = selectedChannels.filter(id => !ids.has(id));
        if (stale.length) {
          update(p => {
            const keep = p.getAll("channel_id").filter(v => !stale.includes(Number(v)));
            p.delete("channel_id");
            keep.forEach(v => p.append("channel_id", v));
          });
        }
      })
      .catch(() => setChannels([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSources.join(",")]);

  useEffect(() => {
    let cancelled = false;
    setBusy(true); setErr(null);
    api.search({
      q: submittedQ || undefined,
      source: selectedSources.length ? selectedSources : undefined,
      channel_id: selectedChannels.length ? selectedChannels : undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      has_predictions: hasPredictions === "" ? undefined : hasPredictions,
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submittedQ, selectedSources.join(","), selectedChannels.join(","),
     dateFrom, dateTo, hasPredictions, page, pageSize]);

  function update(mut: (p: URLSearchParams) => void) {
    const next = new URLSearchParams(params);
    mut(next);
    setParams(next);
  }
  // Any filter change resets to page 1.
  function updateFirstPage(mut: (p: URLSearchParams) => void) {
    update(p => { p.delete("page"); mut(p); });
  }
  function toggleMulti(name: string, value: string) {
    updateFirstPage(p => {
      const cur = p.getAll(name);
      p.delete(name);
      const next = cur.includes(value) ? cur.filter(v => v !== value) : [...cur, value];
      next.forEach(v => p.append(name, v));
    });
  }
  function toggleSource(code: string) { toggleMulti("source", code); }
  function toggleChannel(id: number) { toggleMulti("channel_id", String(id)); }
  function toggleHasPredictions() {
    updateFirstPage(p => {
      if (hasPredictions === "true") p.delete("has_predictions");
      else p.set("has_predictions", "true");
    });
  }

  function submitQuery(e?: React.FormEvent) {
    e?.preventDefault();
    updateFirstPage(p => {
      const t = qInput.trim();
      if (t) p.set("q", t); else p.delete("q");
    });
  }
  function clearQuery() {
    setQInput("");
    updateFirstPage(p => p.delete("q"));
  }
  function clearFilters() {
    setQInput("");
    update(p => {
      ["q", "source", "channel_id", "date_from", "date_to", "has_predictions", "page"]
        .forEach(k => p.delete(k));
    });
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const channelsBySource = useMemo(() => {
    const m = new Map<string, Channel[]>();
    for (const c of channels) {
      if (!m.has(c.source)) m.set(c.source, []);
      m.get(c.source)!.push(c);
    }
    return m;
  }, [channels]);

  const hasFilters = selectedSources.length > 0 || selectedChannels.length > 0
    || !!dateFrom || !!dateTo || hasPredictions !== "" || !!submittedQ;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-6">
      <div className="space-y-4 min-w-0">
        <form onSubmit={submitQuery} className="flex gap-2">
          <input value={qInput} onChange={e => setQInput(e.target.value)}
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

        <ErrorBanner error={err} />

        <div className="flex items-center justify-between text-sm text-mute gap-2 flex-wrap">
          <span>
            {busy && !items.length ? "Loading…" : total === 0 ? "No results." :
              `Showing ${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}`}
          </span>
          <label className="flex items-center gap-2">
            Rows per page
            <select value={pageSize}
              onChange={e => updateFirstPage(p => p.set("page_size", e.target.value))}
              className="bg-panel border border-border rounded px-2 py-1">
              {PAGE_SIZES.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </div>

        {busy && items.length === 0 && <Spinner label="Searching…" />}

        <ul className="space-y-3">
          {items.map(h => (
            <li key={h.id} className="border border-border rounded p-3 bg-panel/40 hover:border-accent/40">
              <div className="text-xs text-mute flex gap-1.5 mb-1 flex-wrap items-baseline">
                <button type="button" onClick={() => toggleSource(h.source)}
                  title={selectedSources.includes(h.source)
                    ? `Remove source filter: ${h.source}` : `Filter by source: ${h.source}`}
                  className={"uppercase hover:underline " +
                    (selectedSources.includes(h.source) ? "text-accent" : "")}>
                  {h.source}
                </button>
                {h.channel_name && h.channel_id != null && (
                  <button type="button" onClick={() => toggleChannel(h.channel_id!)}
                    title={selectedChannels.includes(h.channel_id)
                      ? `Remove channel filter: ${h.channel_name}` : `Filter by channel: ${h.channel_name}`}
                    className={"hover:underline " +
                      (selectedChannels.includes(h.channel_id) ? "text-accent" : "")}>
                    · {h.channel_name}
                  </button>
                )}
                {h.published_at ? (
                  <button type="button"
                    onClick={() => updateFirstPage(p => {
                      p.set("date_from", h.published_at!.slice(0, 10));
                      p.set("date_to", h.published_at!.slice(0, 10));
                    })}
                    title="Filter to this date"
                    className={"hover:underline " +
                      (dateFrom === h.published_at.slice(0, 10) &&
                       dateTo === h.published_at.slice(0, 10) ? "text-accent" : "")}>
                    · {h.published_at.slice(0, 10)}
                  </button>
                ) : (
                  <span title="No publish date in the source metadata">· undated</span>
                )}
                {h.has_predictions && (
                  <button type="button" onClick={() => toggleHasPredictions()}
                    className={"normal-case hover:underline " + (hasPredictions ? "text-accent" : "text-accent/70")}
                    title="Toggle the with-predictions filter">
                    · has predictions
                  </button>
                )}
              </div>
              <Link to={`/items/${h.id}`} className="text-lg hover:underline">
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

        {!busy && items.length === 0 && (
          <div className="border border-dashed border-border rounded p-8 text-center text-mute">
            Nothing found{hasFilters ? " — try clearing some filters." : "."}
          </div>
        )}

        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-3 text-sm pt-2">
            <button disabled={page <= 1} onClick={() => update(p => p.set("page", String(page - 1)))}
              className="border border-border rounded px-3 py-1 disabled:opacity-40">
              Prev
            </button>
            <span className="text-mute">Page {page} of {totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => update(p => p.set("page", String(page + 1)))}
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
                onChange={e => updateFirstPage(
                  p => e.target.value ? p.set("date_from", e.target.value) : p.delete("date_from"))}
                className="flex-1 bg-panel border border-border rounded px-2 py-1" />
            </label>
            <label className="flex items-center gap-2">
              <span className="text-mute text-xs w-8">To</span>
              <input type="date" value={dateTo}
                onChange={e => updateFirstPage(
                  p => e.target.value ? p.set("date_to", e.target.value) : p.delete("date_to"))}
                className="flex-1 bg-panel border border-border rounded px-2 py-1" />
            </label>
          </div>
        </section>

        <section>
          <h4 className="text-mute text-xs uppercase tracking-wide mb-2">Prediction extraction</h4>
          <select value={hasPredictions}
            onChange={e => updateFirstPage(
              p => e.target.value ? p.set("has_predictions", e.target.value) : p.delete("has_predictions"))}
            className="w-full bg-panel border border-border rounded px-2 py-1">
            <option value="">All items</option>
            <option value="true">With predictions</option>
            <option value="false">Without predictions</option>
            <option value="bull">Bullish calls only</option>
            <option value="bear">Bearish calls only</option>
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
