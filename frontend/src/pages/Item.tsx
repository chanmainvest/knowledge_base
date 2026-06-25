import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, Item } from "../api";

export function ItemPage() {
  const { id } = useParams();
  const [item, setItem] = useState<Item | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.item(Number(id)).then(setItem).catch(e => setErr(String(e)));
  }, [id]);

  if (err) return <div className="text-red-400">{err}</div>;
  if (!item) return <div className="text-mute">Loading…</div>;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-8">
      <article>
        <div className="text-xs text-mute mb-1 uppercase">
          {item.source} {item.channel_name && <>· {item.channel_name}</>}
          {item.published_at && <> · {item.published_at.slice(0, 10)}</>}
        </div>
        <h1 className="text-2xl font-semibold mb-2">{item.title}</h1>
        {item.url && (
          <a href={item.url} className="text-accent text-sm" target="_blank" rel="noreferrer">
            Original ↗
          </a>
        )}
        {item.summary && (
          <div className="mt-4 p-3 bg-panel border border-border rounded text-sm">
            <div className="text-mute mb-1">Summary</div>
            {item.summary}
          </div>
        )}
        <div className="prose-kb mt-6">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content || ""}</ReactMarkdown>
        </div>
      </article>

      <aside className="space-y-5 text-sm">
        {item.predictions.length > 0 && (
          <Section title={`Predictions (${item.predictions.length})`}>
            <ul className="space-y-2">
              {item.predictions.map(p => (
                <li key={p.id} className="border border-border rounded p-2 bg-panel/40">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-accent">{p.ticker || "—"}</span>
                    <span className="text-xs uppercase text-mute">
                      {p.action} {p.direction !== "unspecified" && p.direction}
                    </span>
                  </div>
                  {p.timeframe && <div className="text-xs text-mute">{p.timeframe}</div>}
                  {p.target_price && <div className="text-xs">target {p.target_price}</div>}
                  {p.score != null && (
                    <div className={"text-xs " + (p.score > 0 ? "text-green-400" :
                      p.score < 0 ? "text-red-400" : "text-mute")}>
                      score {p.score.toFixed(2)}
                    </div>
                  )}
                  {p.quote && <div className="text-xs text-mute mt-1 italic">"{p.quote}"</div>}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {item.market_views.length > 0 && (
          <Section title="Market views">
            <ul className="space-y-2">
              {item.market_views.map(v => (
                <li key={v.id} className="border border-border rounded p-2 bg-panel/40">
                  <div className="flex justify-between">
                    <span>{v.asset_class || "—"}{v.region ? ` · ${v.region}` : ""}</span>
                    <span className={
                      v.direction === "bullish" ? "text-green-400" :
                      v.direction === "bearish" ? "text-red-400" : "text-mute"
                    }>{v.direction}</span>
                  </div>
                  {v.rationale && <div className="text-xs text-mute mt-1">{v.rationale}</div>}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {item.entities.length > 0 && (
          <Section title="Entities">
            <div className="flex flex-wrap gap-1">
              {item.entities.map(e => (
                <span key={e.id} className="text-xs px-2 py-0.5 rounded bg-panel border border-border">
                  {e.name}{e.ticker ? ` · ${e.ticker}` : ""}
                </span>
              ))}
            </div>
          </Section>
        )}

        {item.related.length > 0 && (
          <Section title="Related">
            <ul className="space-y-1">
              {item.related.map(r => (
                <li key={r.id}>
                  <Link to={`/items/${r.id}`} className="text-accent text-sm hover:underline">
                    {r.title}
                  </Link>
                  <div className="text-xs text-mute">
                    {r.channel_name} · sim {r.similarity.toFixed(2)}
                  </div>
                </li>
              ))}
            </ul>
          </Section>
        )}
      </aside>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-mute text-xs uppercase tracking-wide mb-2">{title}</h3>
      {children}
    </section>
  );
}
