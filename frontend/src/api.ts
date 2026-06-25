const BASE = "";   // Vite proxies /api in dev; same-origin in prod.

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export const api = {
  search: (q: string, source?: string, channel_id?: number) => {
    const p = new URLSearchParams({ q });
    if (source) p.set("source", source);
    if (channel_id) p.set("channel_id", String(channel_id));
    return get<SearchHit[]>(`/api/search?${p}`);
  },
  sources: () => get<Source[]>("/api/sources"),
  channels: (source?: string) => get<Channel[]>(`/api/channels${source ? `?source=${source}` : ""}`),
  item: (id: number) => get<Item>(`/api/items/${id}`),
  items: (q: { source?: string; channel_id?: number; limit?: number; offset?: number } = {}) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && p.set(k, String(v)));
    return get<Item[]>(`/api/items?${p}`);
  },
  predictions: (q: { ticker?: string; channel_id?: number; limit?: number } = {}) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && p.set(k, String(v)));
    return get<Prediction[]>(`/api/predictions?${p}`);
  },
  leaderboard: (weeks = 12) => get<LB>(`/api/leaderboard?weeks=${weeks}`),
};

export interface Source { id: number; code: string; name: string; kind: string; n_items: number; }
export interface Channel { id: number; handle: string; name: string; source: string; n_items: number; }
export interface SearchHit {
  id: number; title: string; url: string; published_at: string | null;
  summary: string | null; source: string; channel: string | null;
  channel_name: string | null; snippet: string; rank: number;
}
export interface Prediction {
  id: number; speaker: string | null; ticker: string | null; asset_name: string | null;
  action: string | null; direction: string | null; target_price: number | null;
  stop_price: number | null; timeframe: string | null; quote: string | null;
  made_at: string | null; price_at_call: number | null; price_at_eval: number | null;
  score: number | null; item_title: string; item_url: string;
  channel: string | null; channel_name: string | null;
}
export interface MarketView {
  id: number; speaker: string | null; asset_class: string | null; region: string | null;
  direction: string | null; horizon: string | null; confidence: number | null;
  rationale: string | null; quote: string | null;
}
export interface Item {
  id: number; title: string; url: string; published_at: string | null;
  summary: string | null; content: string; source: string;
  channel: string | null; channel_name: string | null;
  market_views: MarketView[]; predictions: Prediction[];
  entities: { id: number; kind: string; name: string; ticker: string | null; weight: number }[];
  related: { id: number; title: string; published_at: string | null;
             channel_name: string | null; similarity: number }[];
}
export interface LBRow {
  channel_id: number; handle: string; name: string; source: string;
  n_calls: number; n_scored: number; avg_score: number | null; hit_rate: number | null;
  week_start?: string;
}
export interface LB { weekly: LBRow[]; overall: LBRow[]; }
