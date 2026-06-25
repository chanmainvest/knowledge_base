import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Prediction } from "../api";

export function PredictionsPage() {
  const [ticker, setTicker] = useState("");
  const [rows, setRows] = useState<Prediction[]>([]);

  async function load() {
    setRows(await api.predictions({ ticker: ticker || undefined, limit: 200 }));
  }
  useEffect(() => { load(); }, []);

  return (
    <div className="space-y-3">
      <form onSubmit={e => { e.preventDefault(); load(); }} className="flex gap-2">
        <input value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
          placeholder="Ticker (e.g. AAPL, GC=F, ^GSPC)"
          className="bg-panel border border-border rounded px-3 py-2 font-mono" />
        <button className="bg-accent text-bg rounded px-3 py-2">Filter</button>
      </form>
      <table className="w-full text-sm border border-border">
        <thead className="bg-panel/60 text-mute">
          <tr>
            <th className="text-left p-2">Date</th>
            <th className="text-left p-2">Ticker</th>
            <th className="text-left p-2">Action</th>
            <th className="text-left p-2">Target</th>
            <th className="text-left p-2">Score</th>
            <th className="text-left p-2">Channel</th>
            <th className="text-left p-2">Source item</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(p => (
            <tr key={p.id} className="border-t border-border hover:bg-panel/30">
              <td className="p-2 text-mute">{p.made_at?.slice(0, 10)}</td>
              <td className="p-2 font-mono text-accent">{p.ticker || "—"}</td>
              <td className="p-2">{p.action} {p.direction !== "unspecified" ? p.direction : ""}</td>
              <td className="p-2 font-mono">{p.target_price ?? ""}</td>
              <td className={"p-2 font-mono " + (p.score == null ? "text-mute"
                : p.score > 0 ? "text-green-400"
                : p.score < 0 ? "text-red-400" : "")}>
                {p.score == null ? "—" : p.score.toFixed(2)}
              </td>
              <td className="p-2 text-mute">{p.channel_name}</td>
              <td className="p-2"><Link to={`/items/${(p as any).item_id}`}
                  className="text-accent">{p.item_title}</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
