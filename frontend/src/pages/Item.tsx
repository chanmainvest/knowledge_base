import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, Item } from "../api";

// Collapse whitespace and trim so an LLM-extracted quote matches the same text
// in the rendered article even when line wrapping / spacing differs.
function norm(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

const FLASH_CLASS = "kb-quote-flash";
let flashTimer: ReturnType<typeof setTimeout> | null = null;

// Remove any previously applied flash highlight, restoring the original text
// node. Safe to call multiple times.
function clearFlash(): void {
  document.querySelectorAll("." + FLASH_CLASS).forEach(el => {
    const parent = el.parentNode;
    if (!parent) return;
    while (el.firstChild) parent.insertBefore(el.firstChild, el);
    parent.normalize();
    parent.removeChild(el);
  });
  if (flashTimer) { clearTimeout(flashTimer); flashTimer = null; }
}

// Find the quote text inside the article body and flash-highlight it. Uses a
// TreeWalker over text nodes; if the full quote isn't found verbatim, falls
// back to matching its first ~50 chars (LLM excerpts occasionally differ from
// the markdown by a word or two). If nothing matches, do nothing.
function flashQuote(container: HTMLElement, rawQuote: string): void {
  const q = norm(rawQuote);
  if (!q) return;
  clearFlash();
  const candidates = [q, q.slice(0, 50)].filter(Boolean);
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let node: Text | null;
  while ((node = walker.nextNode() as Text | null)) {
    const t = norm(node.nodeValue || "");
    const probe = candidates.find(c => t.includes(c));
    if (!probe) continue;
    const idx = t.indexOf(probe);
    // Map back into the un-normalized nodeValue via character offsets is fiddly;
    // simplest robust approach: rebuild the text node from the normalized match.
    const before = t.slice(0, idx);
    const match = t.slice(idx, idx + probe.length);
    const after = t.slice(idx + probe.length);
    const span = document.createElement("span");
    span.className = FLASH_CLASS;
    span.textContent = match;
    const parent = node.parentNode;
    if (!parent) continue;
    parent.insertBefore(document.createTextNode(before), node);
    parent.insertBefore(span, node);
    parent.insertBefore(document.createTextNode(after), node);
    parent.removeChild(node);
    span.scrollIntoView({ behavior: "smooth", block: "center" });
    flashTimer = setTimeout(clearFlash, 2800);
    return;
  }
}

export function ItemPage() {
  const { id } = useParams();
  const [item, setItem] = useState<Item | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const articleRef = useRef<HTMLDivElement>(null);

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
        <div className="prose-kb mt-6" ref={articleRef}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content || ""}</ReactMarkdown>
        </div>
      </article>

      <aside className="space-y-5 text-sm">
        {item.predictions.length > 0 && (
          <Section title={`Predictions (${item.predictions.length})`}>
            <ul className="space-y-2">
              {item.predictions.map((p, i) => (
                <li key={(p.ticker || `__${i}`)} className="border border-border rounded p-2 bg-panel/40">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-accent">{p.ticker || "—"}</span>
                    <div className="flex items-center gap-1">
                      {p.conflict && (
                        <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300"
                              title="Same ticker has opposing calls in this article">
                          conflict
                        </span>
                      )}
                      <span className={
                        p.direction === "up" ? "text-xs uppercase text-green-400" :
                        p.direction === "down" ? "text-xs uppercase text-red-400" :
                        p.direction === "mixed" ? "text-xs uppercase text-amber-300" :
                        "text-xs uppercase text-mute"
                      }>{p.direction}</span>
                    </div>
                  </div>
                  {p.asset_name && <div className="text-xs text-mute">{p.asset_name}</div>}
                  {p.quotes.length > 0 && (
                    <ul className="mt-1 space-y-1">
                      {p.quotes.map(q => (
                        <li key={q.id} className="text-xs">
                          <div className="flex items-center gap-2 text-mute">
                            <span className="uppercase">{q.action}</span>
                            {q.direction && q.direction !== "unspecified" &&
                              <span>· {q.direction}</span>}
                            {q.timeframe && <span>· {q.timeframe}</span>}
                            {q.target_price && <span>· tgt {q.target_price}</span>}
                            {q.score != null && (
                              <span className={q.score > 0 ? "text-green-400" :
                                q.score < 0 ? "text-red-400" : "text-mute"}>
                                · {q.score.toFixed(2)}
                              </span>
                            )}
                          </div>
                          {q.quote && (
                            <button type="button"
                              onClick={() => articleRef.current && flashQuote(articleRef.current, q.quote || "")}
                              className="mt-0.5 text-left italic text-mute hover:text-accent hover:underline cursor-pointer"
                              title="Jump to this quote in the article">
                              "{q.quote}"
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
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
