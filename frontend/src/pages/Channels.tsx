import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Channel, Source } from "../api";

export function ChannelsPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [src, setSrc] = useState<string>("");
  const [channels, setChannels] = useState<Channel[]>([]);

  useEffect(() => { api.sources().then(setSources); }, []);
  useEffect(() => {
    api.channels(src || undefined).then(setChannels).catch(() => setChannels([]));
  }, [src]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <select value={src} onChange={e => setSrc(e.target.value)}
          className="bg-panel border border-border rounded px-2 py-2">
          <option value="">All sources</option>
          {sources.map(s => <option key={s.id} value={s.code}>{s.name}</option>)}
        </select>
        <span className="text-mute text-sm">{channels.length} channels</span>
      </div>
      <table className="w-full text-sm border border-border">
        <thead className="bg-panel/60 text-mute">
          <tr><th className="text-left p-2">Channel</th>
              <th className="text-left p-2">Source</th>
              <th className="text-right p-2">Items</th></tr>
        </thead>
        <tbody>
          {channels.map(c => (
            <tr key={c.id} className="border-t border-border hover:bg-panel/30">
              <td className="p-2">
                <Link to={`/search?channel_id=${c.id}`} className="text-accent">{c.name}</Link>
                <div className="text-xs text-mute">{c.handle}</div>
              </td>
              <td className="p-2 uppercase text-mute">{c.source}</td>
              <td className="p-2 text-right font-mono">{c.n_items}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
