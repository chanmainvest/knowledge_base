import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Source, Channel, SearchHit } from "../api";

export function SearchPage() {
  const [q, setQ] = useState("");
  const [src, setSrc] = useState<string>("");
  const [chan, setChan] = useState<string>("");
  const [sources, setSources] = useState<Source[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.sources().then(setSources).catch(() => {}); }, []);
  useEffect(() => {
    api.channels(src || undefined).then(setChannels).catch(() => setChannels([]));
    setChan("");
  }, [src]);

  async function run(e?: React.FormEvent) {
    e?.preventDefault();
    if (!q.trim()) return;
    setBusy(true); setErr(null);
    try {
      const h = await api.search(q, src || undefined, chan ? Number(chan) : undefined);
      setHits(h);
    } catch (ex: any) {
      setErr(String(ex?.message || ex));
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={run} className="flex flex-wrap gap-2 items-center">
        <input value={q} onChange={e => setQ(e.target.value)}
          placeholder="Search transcripts and articles…"
          className="flex-1 min-w-[240px] bg-panel border border-border rounded px-3 py-2 outline-none focus:border-accent" />
        <select value={src} onChange={e => setSrc(e.target.value)}
          className="bg-panel border border-border rounded px-2 py-2">
          <option value="">All sources</option>
          {sources.map(s => <option key={s.id} value={s.code}>{s.name} ({s.n_items})</option>)}
        </select>
        <select value={chan} onChange={e => setChan(e.target.value)}
          className="bg-panel border border-border rounded px-2 py-2 max-w-[260px]">
          <option value="">All channels</option>
          {channels.map(c => <option key={c.id} value={c.id}>{c.name} ({c.n_items})</option>)}
        </select>
        <button disabled={busy} className="bg-accent text-bg font-medium rounded px-4 py-2">
          {busy ? "…" : "Search"}
        </button>
      </form>
      {err && <div className="text-red-400 text-sm">{err}</div>}
      {hits && hits.length === 0 && <div className="text-mute">No results.</div>}
      <ul className="space-y-3">
        {hits?.map(h => (
          <li key={h.id} className="border border-border rounded p-3 bg-panel/40">
            <div className="text-xs text-mute flex gap-2 mb-1">
              <span className="uppercase">{h.source}</span>
              {h.channel_name && <span>· {h.channel_name}</span>}
              {h.published_at && <span>· {h.published_at.slice(0, 10)}</span>}
            </div>
            <Link to={`/items/${h.id}`} className="text-lg text-accent hover:underline">
              {h.title}
            </Link>
            <div className="snippet text-sm mt-1"
                 dangerouslySetInnerHTML={{ __html: h.snippet || "" }} />
          </li>
        ))}
      </ul>
    </div>
  );
}
