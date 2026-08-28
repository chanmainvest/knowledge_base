import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";

// Floating "chat with this article" bubble, bottom-right, for the item page.
// Conversation state is local to the mount (navigating to another item
// starts a fresh chat); the backend is stateless.
export function ChatWidget({ itemId, title }: { itemId: number; title: string }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Fresh conversation when the item changes.
  useEffect(() => { setMessages([]); setErr(null); }, [itemId]);

  // Keep the latest message visible.
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages, busy, open]);

  function send(e?: React.FormEvent) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    const next = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setBusy(true); setErr(null);
    api.chat(itemId, next)
      .then(r => setMessages(m => [...m, { role: "assistant", content: r.reply }]))
      .catch(ex => setErr(String(ex?.message || ex)))
      .finally(() => setBusy(false));
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}
        title="Chat with this article"
        className="fixed bottom-5 right-5 z-50 w-12 h-12 rounded-full bg-accent text-bg
                   text-xl font-bold shadow-lg hover:brightness-110 flex items-center justify-center">
        💬
      </button>
    );
  }

  // Drag-to-resize from the top-left grip: the panel is anchored bottom-right
  // (fixed bottom/right), so growing means moving the top-left corner up/left.
  function startResize(e: React.PointerEvent) {
    const el = panelRef.current;
    if (!el) return;
    e.preventDefault();
    const startX = e.clientX, startY = e.clientY;
    const startW = el.offsetWidth, startH = el.offsetHeight;
    const move = (ev: PointerEvent) => {
      const w = Math.min(Math.max(startW - (ev.clientX - startX), 288), window.innerWidth - 40);
      const h = Math.min(Math.max(startH - (ev.clientY - startY), 288), window.innerHeight - 40);
      el.style.width = w + "px";
      el.style.height = h + "px";
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <div ref={panelRef} className="fixed bottom-5 right-5 z-50 flex flex-col
                    border border-border rounded-lg bg-panel shadow-xl overflow-hidden
                    w-[min(24rem,calc(100vw-2.5rem))] h-[min(32rem,calc(100vh-6rem))]">
      <div className="absolute -top-0.5 -left-0.5 w-5 h-5 cursor-nwse-resize z-10
                      text-mute hover:text-accent" onPointerDown={startResize}
           title="Drag to resize">
        <svg viewBox="0 0 16 16" className="w-full h-full rotate-90" fill="currentColor">
          <circle cx="3" cy="3" r="1.4"/><circle cx="8" cy="3" r="1.4"/><circle cx="3" cy="8" r="1.4"/>
        </svg>
      </div>
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-border">
        <div className="min-w-0">
          <div className="text-sm font-semibold">Chat with this article</div>
          <div className="text-xs text-mute truncate">{title}</div>
        </div>
        <button type="button" onClick={() => setOpen(false)}
          className="text-mute hover:text-ink text-lg leading-none px-1" title="Close">
          ×
        </button>
      </div>

      <div ref={listRef} className="flex-1 overflow-y-auto p-3 space-y-2 text-sm">
        {messages.length === 0 && (
          <div className="text-mute text-xs">
            Ask anything — grounded in this article where possible, general
            knowledge otherwise (glm-5.3-flash).
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i}
            className={m.role === "user"
              ? "ml-8 bg-accent/15 border border-accent/30 rounded px-2.5 py-1.5 whitespace-pre-wrap"
              : "mr-4 bg-bg border border-border rounded px-2.5 py-1.5"}>
            {m.role === "user"
              ? m.content
              : <div className="prose-kb text-sm max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                </div>}
          </div>
        ))}
        {busy && <div className="text-mute text-xs animate-pulse">thinking…</div>}
        {err && <div className="text-red-400 text-xs">{err}</div>}
      </div>

      <form onSubmit={send} className="flex gap-2 p-2 border-t border-border">
        <input value={input} onChange={e => setInput(e.target.value)}
          placeholder="Ask about this article…"
          className="flex-1 min-w-0 bg-bg border border-border rounded px-2.5 py-1.5
                     text-sm outline-none focus:border-accent" />
        <button disabled={busy || !input.trim()} title="Send"
          className="bg-accent text-bg rounded px-3 py-1.5 disabled:opacity-40 shrink-0">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M2.01 21 23 12 2.01 3 2 10l15 2-15 2z" />
          </svg>
        </button>
      </form>
    </div>
  );
}
