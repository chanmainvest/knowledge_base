import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, Channel, Prediction, PredictionsResult, TickerStat } from "../api";
import { Spinner, ErrorBanner, Pager, ScoreSpan, useTitle } from "../components/ui";

// Rough client-side mirror of leaderboard._horizon_days, only used to decide
// whether an unscored call is "waiting" (horizon not elapsed) or just unscored.
function horizonText(timeframe: string | null | undefined): string {
  const s = (timeframe || "").toLowerCase();
  if (s.includes("day") || s.endsWith("d")) return "7d";
  if (s.includes("week") || s.includes("wk")) return "14d";
  if (s.includes("month") || s.endsWith("m")) return "90d";
  if (s.includes("quarter") || s.includes("q")) return "90d";
  if (s.includes("year") || s.endsWith("y")) return "1y";
  return "90d";
}

function Stance({ action, direction }: { action: string | null; direction: string | null }) {
  const a = (action || "").toLowerCase();
  const d = (direction || "").toLowerCase();
  let cls = "text-mute";
  let label = a || d || "—";
  if (d === "up" || ["buy", "long", "cover"].includes(a)) { cls = "text-green-400"; label = (a || d).toUpperCase(); }
  else if (d === "down" || ["sell", "short", "avoid"].includes(a)) { cls = "text-red-400"; label = (a || d).toUpperCase(); }
  return <span className={"text-xs font-semibold " + cls}>{label}</span>;
}

// Call price → eval price with the move in the call's own direction.
// Prices arrive as JSON numbers, but guard with Number() in case a NUMERIC
// sneaks through as a string (that crashed the page once).
function PriceMove({ p }: { p: Prediction }) {
  const call = p.price_at_call != null ? Number(p.price_at_call) : null;
  const evalP = p.price_at_eval != null ? Number(p.price_at_eval) : null;
  if (call == null || isNaN(call)) return <span className="text-mute">—</span>;
  const fmtP = (v: number) => v >= 1000 ? v.toFixed(0) : v.toFixed(2);
  const move = evalP != null && !isNaN(evalP) && call
    ? (evalP - call) / call : null;
  return (
    <span className="font-mono text-xs whitespace-nowrap">
      {fmtP(call)}
      {evalP != null && !isNaN(evalP) && <> → {fmtP(evalP)}</>}
      {move != null && (
        <span className={move > 0 ? " text-green-400" : move < 0 ? " text-red-400" : " text-mute"}>
          {" "}({move > 0 ? "+" : ""}{(move * 100).toFixed(1)}%)
        </span>
      )}
    </span>
  );
}

const DIRECTIONS = ["up", "down", "flat", "unspecified"];

export function PredictionsPage() {
  useTitle("Predictions");
  const [urlParams] = useSearchParams();

  const [ticker, setTicker] = useState(urlParams.get("ticker") || "");
  const [speaker, setSpeaker] = useState(urlParams.get("speaker") || "");
  const [channelId, setChannelId] = useState<number | "">(Number(urlParams.get("channel_id")) || "");
  const [direction, setDirection] = useState(urlParams.get("direction") || "");
  const [scored, setScored] = useState<"" | "true" | "false">(
    urlParams.get("scored") === "true" || urlParams.get("scored") === "false"
      ? urlParams.get("scored") as "true" | "false" : "");
  const [order, setOrder] = useState<"recent" | "score_desc" | "score_asc">("recent");

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const [channels, setChannels] = useState<Channel[]>([]);
  const [tickers, setTickers] = useState<TickerStat[]>([]);
  const [data, setData] = useState<PredictionsResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.channels().then(setChannels).catch(() => {}); }, []);
  useEffect(() => { api.marketTickers().then(setTickers).catch(() => {}); }, []);

  // Server-driven: refetch on any filter/page change.
  useEffect(() => {
    let cancelled = false;
    setBusy(true); setErr(null);
    api.predictions({
      ticker: ticker || undefined,
      speaker: speaker || undefined,
      channel_id: channelId === "" ? undefined : channelId,
      direction: direction || undefined,
      scored: scored === "" ? undefined : scored === "true",
      order,
      limit: pageSize,
      offset: (page - 1) * pageSize,
    }).then(res => { if (!cancelled) setData(res); })
      .catch(e => { if (!cancelled) setErr(String(e?.message || e)); })
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [ticker, speaker, channelId, direction, scored, order, page, pageSize]);

  function resetPage<R>(setter: (v: R) => void) {
    return (v: R) => { setPage(1); setter(v); };
  }
  const hasFilters = !!(ticker || speaker || channelId !== "" || direction || scored !== "");
  function clearFilters() {
    setTicker(""); setSpeaker(""); setChannelId(""); setDirection(""); setScored(""); setPage(1);
  }

  const knownTickers = useMemo(() => tickers.map(t => t.ticker).slice(0, 200), [tickers]);

  const rows = data?.items ?? [];
  const total = data?.total ?? 0;

  const inputCls = "bg-panel border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent";
  const selectCls = inputCls + " pr-6";

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold">Predictions</h1>
          <div className="text-mute text-sm mt-0.5">
            Every extracted market call, scored against the price store.
          </div>
        </div>
        {hasFilters && (
          <button onClick={clearFilters} className="text-accent text-sm hover:underline">
            Clear filters
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2 items-center">
        <div>
          <label className="block text-mute text-xs mb-1">Ticker</label>
          <input list="kb-tickers" value={ticker}
            onChange={e => resetPage(setTicker)(e.target.value.toUpperCase())}
            placeholder="e.g. GC=F" className={inputCls + " font-mono w-full"} />
          <datalist id="kb-tickers">
            {knownTickers.map(t => <option key={t} value={t} />)}
          </datalist>
        </div>
        <div>
          <label className="block text-mute text-xs mb-1">Speaker</label>
          <input value={speaker} onChange={e => resetPage(setSpeaker)(e.target.value)}
            placeholder="name contains…" className={inputCls + " w-full"} />
        </div>
        <div>
          <label className="block text-mute text-xs mb-1">Channel</label>
          <select value={channelId} onChange={e => resetPage(setChannelId)(
              e.target.value === "" ? "" : Number(e.target.value))}
            className={selectCls + " w-full"}>
            <option value="">All channels</option>
            {channels.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-mute text-xs mb-1">Direction</label>
          <select value={direction} onChange={e => resetPage(setDirection)(e.target.value)}
            className={selectCls + " w-full"}>
            <option value="">Any</option>
            {DIRECTIONS.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-mute text-xs mb-1">Scored</label>
          <select value={scored} onChange={e => resetPage(setScored)(e.target.value as any)}
            className={selectCls + " w-full"}>
            <option value="">All</option>
            <option value="true">Scored only</option>
            <option value="false">Unscored only</option>
          </select>
        </div>
        <div>
          <label className="block text-mute text-xs mb-1">Sort by</label>
          <select value={order} onChange={e => resetPage(setOrder)(e.target.value as any)}
            className={selectCls + " w-full"}>
            <option value="recent">Newest</option>
            <option value="score_desc">Best score</option>
            <option value="score_asc">Worst score</option>
          </select>
        </div>
      </div>

      <ErrorBanner error={err} />
      {busy && !data && <Spinner label="Loading predictions…" />}

      {data && (
        <>
          <Pager page={page} pageSize={pageSize} total={total}
            onPage={setPage} onPageSize={n => { setPageSize(n); setPage(1); }} />
          <div className="overflow-x-auto">
            <table className="w-full text-sm border border-border">
              <thead className="bg-panel/60 text-mute">
                <tr>
                  <th className="text-left p-2">Date</th>
                  <th className="text-left p-2">Ticker</th>
                  <th className="text-left p-2">Speaker</th>
                  <th className="text-left p-2">Stance</th>
                  <th className="text-left p-2">Target</th>
                  <th className="text-left p-2">Horizon</th>
                  <th className="text-left p-2">Price call → eval</th>
                  <th className="text-right p-2">Score</th>
                  <th className="text-left p-2">Channel</th>
                  <th className="text-left p-2">Item</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(p => (
                  <tr key={p.id} className="border-t border-border hover:bg-panel/30 align-top">
                    <td className="p-2 text-mute whitespace-nowrap">{p.made_at?.slice(0, 10) ?? "—"}</td>
                    <td className="p-2">
                      <span className="font-mono text-accent">{p.ticker || "—"}</span>
                      {p.asset_name && <div className="text-xs text-mute">{p.asset_name}</div>}
                    </td>
                    <td className="p-2 whitespace-nowrap">
                      {p.speaker &&
                        <Link to={`/predictions?speaker=${encodeURIComponent(p.speaker)}`}
                          className="hover:underline">{p.speaker}</Link>}
                    </td>
                    <td className="p-2"><Stance action={p.action} direction={p.direction} /></td>
                    <td className="p-2 font-mono text-xs">{p.target_price ?? ""}</td>
                    <td className="p-2 text-mute text-xs">{p.timeframe || "—"}</td>
                    <td className="p-2"><PriceMove p={p} /></td>
                    <td className="p-2 text-right">
                      {p.score != null ? <ScoreSpan v={p.score} />
                        : <span className="text-mute text-xs" title={`Horizon ${horizonText(p.timeframe)} not elapsed / no price data`}>pending</span>}
                    </td>
                    <td className="p-2 text-mute text-xs whitespace-nowrap">{p.channel_name}</td>
                    <td className="p-2 max-w-[24rem]">
                      <Link to={`/items/${p.item_id}`} className="text-accent hover:underline line-clamp-2">
                        {p.item_title}
                      </Link>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={10} className="p-6 text-center text-mute">
                      No predictions match the current filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-mute">
            Score = direction sign × price move × 5, clamped to ±1 (a 20% move in the called
            direction over the horizon is a full score). Calls whose horizon hasn't elapsed
            score against the latest close and keep updating until it does. Hold/watch quotes
            are not scored.
          </p>
        </>
      )}
    </div>
  );
}
