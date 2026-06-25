import { useEffect, useMemo, useState } from "react";
import { api, LB } from "../api";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid,
} from "recharts";

const COLORS = ["#5cc8ff", "#ffd55c", "#ff7eb6", "#7ee787", "#a371f7",
                "#f97583", "#79b8ff", "#bfa3ff", "#ffa657", "#56d364"];

export function LeaderboardPage() {
  const [data, setData] = useState<LB | null>(null);
  const [weeks, setWeeks] = useState(12);

  useEffect(() => { api.leaderboard(weeks).then(setData); }, [weeks]);

  const chartData = useMemo(() => {
    if (!data) return [] as any[];
    const byWeek: Record<string, any> = {};
    for (const r of data.weekly) {
      const wk = r.week_start || "";
      if (!byWeek[wk]) byWeek[wk] = { week_start: wk };
      byWeek[wk][r.name] = r.avg_score;
    }
    return Object.values(byWeek).sort((a: any, b: any) =>
      a.week_start.localeCompare(b.week_start));
  }, [data]);

  const topNames = useMemo(() => {
    if (!data) return [] as string[];
    return [...data.overall].slice(0, 10).map(r => r.name);
  }, [data]);

  if (!data) return <div className="text-mute">Loading…</div>;

  return (
    <div className="space-y-6">
      <div className="flex gap-2 items-center">
        <label className="text-mute text-sm">Window:</label>
        {[4, 12, 26, 52].map(w => (
          <button key={w} onClick={() => setWeeks(w)}
            className={"px-2 py-1 rounded text-sm border border-border " +
              (weeks === w ? "bg-accent text-bg" : "bg-panel hover:bg-panel/70")}>
            {w}w
          </button>
        ))}
      </div>

      <section>
        <h2 className="text-lg font-semibold mb-2">Avg score by channel (weekly)</h2>
        <div className="h-72 bg-panel/40 border border-border rounded p-2">
          <ResponsiveContainer>
            <LineChart data={chartData}>
              <CartesianGrid stroke="#222a33" />
              <XAxis dataKey="week_start" stroke="#8a96a3" />
              <YAxis stroke="#8a96a3" domain={[-1, 1]} />
              <Tooltip contentStyle={{ background: "#13171c", border: "1px solid #222a33" }} />
              <Legend />
              {topNames.map((n, i) => (
                <Line key={n} type="monotone" dataKey={n} stroke={COLORS[i % COLORS.length]}
                      dot={false} strokeWidth={2} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-2">All-time leaderboard</h2>
        <table className="w-full text-sm border border-border">
          <thead className="bg-panel/60 text-mute">
            <tr>
              <th className="text-left p-2">#</th>
              <th className="text-left p-2">Channel</th>
              <th className="text-left p-2">Source</th>
              <th className="text-right p-2">Calls</th>
              <th className="text-right p-2">Scored</th>
              <th className="text-right p-2">Avg score</th>
              <th className="text-right p-2">Hit rate</th>
            </tr>
          </thead>
          <tbody>
            {data.overall.map((r, i) => (
              <tr key={r.channel_id} className="border-t border-border hover:bg-panel/30">
                <td className="p-2 text-mute">{i + 1}</td>
                <td className="p-2">{r.name}</td>
                <td className="p-2 uppercase text-mute">{r.source}</td>
                <td className="p-2 text-right font-mono">{r.n_calls}</td>
                <td className="p-2 text-right font-mono">{r.n_scored}</td>
                <td className={"p-2 text-right font-mono " + (
                  r.avg_score == null ? "text-mute" :
                  r.avg_score > 0 ? "text-green-400" :
                  r.avg_score < 0 ? "text-red-400" : "")}>
                  {r.avg_score == null ? "—" : r.avg_score.toFixed(3)}
                </td>
                <td className="p-2 text-right font-mono">
                  {r.hit_rate == null ? "—" : (r.hit_rate * 100).toFixed(0) + "%"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
