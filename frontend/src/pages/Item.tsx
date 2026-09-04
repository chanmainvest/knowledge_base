import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { LineChart, Line, ResponsiveContainer, YAxis } from "recharts";
import { api, Item, PricePoint } from "../api";
import { ErrorBanner, Spinner, useTitle } from "../components/ui";
import { ChatWidget } from "../components/ChatWidget";

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

// Tiny price sparkline since the call date, from the market price store.
// Renders nothing when the ticker has no cached prices.
function Sparkline({ ticker, madeAt, up }: { ticker: string; madeAt: string | null; up: boolean }) {
  const [points, setPoints] = useState<PricePoint[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    if (!madeAt) return;
    const from = new Date(new Date(madeAt).getTime() - 7 * 86400_000).toISOString().slice(0, 10);
    api.marketPrices(ticker, from)
      .then(r => { if (!cancelled) setPoints(r.points); })
      .catch(() => { if (!cancelled) setPoints([]); });
    return () => { cancelled = true; };
  }, [ticker, madeAt]);
  if (!points) return null;
  const withClose = points.filter(p => p.close != null);
  if (withClose.length < 2) return null;
  const first = withClose[0].close!, last = withClose[withClose.length - 1].close!;
  const pct = ((last - first) / first) * 100;
  const color = up ? "var(--kb-up)" : "var(--kb-down)";
  return (
    <div className="w-28 h-8 shrink-0" title={`Price since call: ${pct >= 0 ? "+" : ""}${pct.toFixed(1)}% (${first.toFixed(2)} → ${last.toFixed(2)})`}>
      <ResponsiveContainer>
        <LineChart data={withClose}>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Line type="monotone" dataKey="close" stroke={color} dot={false} strokeWidth={1.5} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// The transcript (YouTube items) is split out of the markdown body and
// rendered in its own collapsible section: very long transcripts default to
// collapsed so the description/summary stays scannable and we don't render
// 50k+ chars of DOM on every item view.
function TranscriptSection({ text }: { text: string }) {
  const [open, setOpen] = useState(text.length <= 24000);
  const words = useMemo(() => text.trim().split(/\s+/).length, [text]);
  return (
    <section className="border-t border-border pt-3 mt-8">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Transcript</h2>
        <button type="button" onClick={() => setOpen(o => !o)}
          className="text-sm text-accent hover:underline">
          {open ? "Hide" : `Show transcript · ${words.toLocaleString()} words`}
        </button>
      </div>
      {open && (
        <div className="mt-2">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
        </div>
      )}
    </section>
  );
}

export function ItemPage() {
  const { id } = useParams();
  const [item, setItem] = useState<Item | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const articleRef = useRef<HTMLDivElement>(null);
  useTitle(item ? item.title.slice(0, 60) : "Item");

  useEffect(() => {
    setItem(null); setErr(null);
    if (!id) return;
    api.item(Number(id)).then(setItem).catch(e => setErr(String(e?.message || e)));
  }, [id]);

  if (err) return <ErrorBanner error={err} />;
  if (!item) return <Spinner label="Loading item…" />;

  // Split the transcript (if any) off the markdown body so it can render as
  // its own collapsible section. 72ch column: comfortable line length for
  // long-form reading.
  const marker = "## Transcript";
  const splitIdx = (item.content || "").indexOf(marker);
  const articleMd = splitIdx < 0
    ? (item.content || "")
    : (item.content || "").slice(0, splitIdx).trimEnd();
  const transcript = splitIdx < 0
    ? null
    : (item.content || "").slice(splitIdx + marker.length).trim();

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-8">
      <ChatWidget target={{ itemId: item.id }} title={item.title} />
      <article className="max-w-[72ch]">
        <div className="text-xs text-mute mb-1 uppercase">
          {item.source} {item.channel_name && <>· {item.channel_name}</>}
          {item.published_at
            ? <> · {item.published_at.slice(0, 10)}</>
            : <> · undated</>}
          {item.is_marketing === true &&
            <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400 normal-case">
              promo material
            </span>}
        </div>
        <h1 className="text-2xl font-semibold mb-2">{item.title}</h1>
        <div className="flex gap-3 items-center">
          {item.url && (
            <a href={item.url} className="text-accent text-sm hover:underline" target="_blank" rel="noreferrer">
              Original ↗
            </a>
          )}
          <a href={`/api/items/${item.id}/raw`} className="text-mute text-sm hover:underline"
             target="_blank" rel="noreferrer">
            Raw markdown ↗
          </a>
        </div>
        {item.summary && (
          <div className="mt-4 p-3 bg-panel border border-border rounded text-sm">
            <div className="text-mute mb-1">Summary</div>
            {item.summary}
          </div>
        )}
        <div className="prose-kb mt-6" ref={articleRef}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{articleMd}</ReactMarkdown>
          {transcript !== null && <TranscriptSection text={transcript} />}
        </div>
      </article>

      <aside className="space-y-5 text-sm">
        {item.predictions.length > 0 && (
          <Section title={`Predictions (${item.predictions.length})`}>
            <ul className="space-y-2">
              {item.predictions.map((p, i) => {
                const madeAt = p.quotes[0]?.made_at ?? item.published_at;
                const up = p.direction === "up";
                return (
                  <li key={(p.ticker || `__${i}`)} className="border border-border rounded p-2 bg-panel/40">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        {p.ticker ? (
                          <Link to={`/predictions?ticker=${encodeURIComponent(p.ticker)}`}
                                title="See all predictions for this ticker"
                                className="font-mono text-accent truncate hover:underline">
                            {p.ticker}
                          </Link>
                        ) : (
                          <span className="font-mono text-accent truncate">—</span>
                        )}
                        {p.conflict && (
                          <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-800 dark:text-amber-300 shrink-0"
                                title="Same ticker has opposing calls in this article">
                            conflict
                          </span>
                        )}
                        <span className={
                          up ? "text-xs uppercase text-green-700 dark:text-green-400" :
                          p.direction === "down" ? "text-xs uppercase text-red-600 dark:text-red-400" :
                          p.direction === "mixed" ? "text-xs uppercase text-amber-800 dark:text-amber-300" :
                          "text-xs uppercase text-mute"
                        }>{p.direction}</span>
                      </div>
                      {p.ticker && <Sparkline ticker={p.ticker} madeAt={madeAt} up={up} />}
                    </div>
                    {p.asset_name && <div className="text-xs text-mute">{p.asset_name}</div>}
                    {p.speaker && (
                      <Link to={`/predictions?speaker=${encodeURIComponent(p.speaker)}`}
                        className="text-xs text-mute hover:text-accent hover:underline">
                        {p.speaker}
                      </Link>
                    )}
                    {p.quotes.length > 0 && (
                      <ul className="mt-1 space-y-1">
                        {p.quotes.map(q => (
                          <li key={q.id} className="text-xs">
                            <div className="flex items-center gap-2 text-mute flex-wrap">
                              <span className="uppercase">{q.action}</span>
                              {q.direction && q.direction !== "unspecified" &&
                                <span>· {q.direction}</span>}
                              {q.timeframe && <span>· {q.timeframe}</span>}
                              {q.target_price && <span>· tgt {q.target_price}</span>}
                              {q.score != null && (
                                <span className={q.score > 0 ? "text-green-700 dark:text-green-400" :
                                  q.score < 0 ? "text-red-600 dark:text-red-400" : "text-mute"}>
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
                );
              })}
            </ul>
          </Section>
        )}

        {item.market_views.length > 0 && (
          <Section title="Market views">
            <ul className="space-y-2">
              {item.market_views.map(v => (
                <li key={v.id} className="border border-border rounded p-2 bg-panel/40">
                  <div className="flex justify-between gap-2">
                    <span className="truncate">{v.asset_class || "—"}{v.region ? ` · ${v.region}` : ""}</span>
                    <span className={
                      v.direction === "bullish" ? "text-green-700 dark:text-green-400" :
                      v.direction === "bearish" ? "text-red-600 dark:text-red-400" : "text-mute"
                    }>{v.direction}</span>
                  </div>
                  {v.speaker && <div className="text-xs text-mute mt-0.5">{v.speaker}</div>}
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
                <span key={e.id}
                  className={"text-xs px-2 py-0.5 rounded bg-panel border border-border " +
                    (e.kind === "person" ? "border-accent/40" : "")}
                  title={e.kind}>
                  {e.name}{e.ticker ? ` · ${e.ticker}` : ""}
                </span>
              ))}
            </div>
          </Section>
        )}

        {(item.media_mentions?.length ?? 0) > 0 && (
          <Section title="Books · Movies · Papers">
            <ul className="space-y-2">
              {item.media_mentions.map(m => (
                <li key={m.work_id} className="text-sm">
                  <span className={"text-[10px] uppercase px-1.5 py-0.5 rounded mr-1.5 " +
                    (m.kind === "book"
                      ? "bg-accent/15 text-accent"
                      : "bg-panel border border-border text-mute")}>
                    {m.kind}
                  </span>
                  <span className="font-medium">{m.title}</span>
                  {m.year ? <span className="text-mute"> ({m.year})</span> : null}
                  {m.creators && <div className="text-xs text-mute">{m.creators}</div>}
                  {m.speaker &&
                    <div className="text-xs text-mute mt-0.5">mentioned by {m.speaker}</div>}
                </li>
              ))}
            </ul>
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
