const BASE = "";   // Vite proxies /api in dev; same-origin in prod.

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

// Shared query shape for /api/search and /api/items: an optional keyword `q`
// (omit it to just browse the latest items), multi-select `source`/`channel_id`,
// an inclusive published_at date range, a with/without-prediction-extraction
// filter, and pagination.
export interface ListQuery {
  q?: string;
  source?: string[];
  channel_id?: number[];
  date_from?: string;
  date_to?: string;
  has_predictions?: boolean;
  limit?: number;
  offset?: number;
}

function listParams(q: ListQuery): URLSearchParams {
  const p = new URLSearchParams();
  if (q.q) p.set("q", q.q);
  for (const s of q.source ?? []) p.append("source", s);
  for (const c of q.channel_id ?? []) p.append("channel_id", String(c));
  if (q.date_from) p.set("date_from", q.date_from);
  if (q.date_to) p.set("date_to", q.date_to);
  if (q.has_predictions !== undefined) p.set("has_predictions", String(q.has_predictions));
  if (q.limit !== undefined) p.set("limit", String(q.limit));
  if (q.offset !== undefined) p.set("offset", String(q.offset));
  return p;
}

export const api = {
  search: (q: ListQuery = {}) => get<SearchResult>(`/api/search?${listParams(q)}`),
  sources: () => get<Source[]>("/api/sources"),
  dashboard: () => get<Dashboard>("/api/dashboard"),
  channels: (source?: string[]) => {
    const p = new URLSearchParams();
    for (const s of source ?? []) p.append("source", s);
    return get<Channel[]>(`/api/channels?${p}`);
  },
  item: (id: number) => get<Item>(`/api/items/${id}`),
  items: (q: ListQuery = {}) => get<SearchResult>(`/api/items?${listParams(q)}`),
  predictions: (q: { ticker?: string; channel_id?: number; limit?: number } = {}) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && p.set(k, String(v)));
    return get<Prediction[]>(`/api/predictions?${p}`);
  },
  leaderboard: (weeks = 12, dateFrom?: string, dateTo?: string) => {
    const p = new URLSearchParams();
    p.set("weeks", String(weeks));
    if (dateFrom) p.set("date_from", dateFrom);
    if (dateTo) p.set("date_to", dateTo);
    return get<LB>(`/api/leaderboard?${p}`);
  },
};

export interface Source { id: number; code: string; name: string; kind: string; n_items: number; }
export interface Channel {
  id: number; handle: string; name: string; source: string; n_items: number;
  n_calls: number; n_scored: number; avg_score: number | null; hit_rate: number | null;
}
export interface SearchHit {
  id: number; title: string; url: string; published_at: string | null;
  summary: string | null; source: string; channel: string | null;
  channel_name: string | null; has_predictions: boolean;
  snippet: string | null; rank: number | null;
}
export interface SearchResult { items: SearchHit[]; total: number; limit: number; offset: number; }
export interface Prediction {
  id: number; speaker: string | null; ticker: string | null; asset_name: string | null;
  action: string | null; direction: string | null; target_price: number | null;
  stop_price: number | null; timeframe: string | null; quote: string | null;
  made_at: string | null; price_at_call: number | null; price_at_eval: number | null;
  score: number | null; item_title: string; item_url: string;
  channel: string | null; channel_name: string | null;
}
// A single quote within a consolidated prediction (one ticker can reference
// several quotes from the same article).
export interface PredictionQuote {
  id: number; action: string | null; direction: string | null;
  target_price: number | null; stop_price: number | null;
  timeframe: string | null; quote: string | null;
  score: number | null; made_at: string | null;
}
// One entry per ticker on the item page, grouping every quote extracted for
// it. `conflict` is true when the same ticker has opposing directional calls.
export interface ConsolidatedPrediction {
  ticker: string | null; asset_name: string | null; speaker: string | null;
  direction: string; conflict: boolean; quotes: PredictionQuote[];
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
  market_views: MarketView[]; predictions: ConsolidatedPrediction[];
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

// Dashboard: per-source pipeline progress (download → ingest → extract).
export interface DashboardSource {
  code: string; name: string; kind: string;
  n_downloaded: number; n_ingested: number; n_extracted: number;
  n_extract_pending: number; n_extract_error: number;
  n_pending_download: number; total_known: number | null;
  last_scrape_at: string | null; last_ingest_at: string | null; last_extract_at: string | null;
}
export interface DashboardTotals {
  n_downloaded: number; n_ingested: number; n_extracted: number;
  n_extract_pending: number; n_extract_error: number; n_pending_download: number;
}
export interface Dashboard { sources: DashboardSource[]; totals: DashboardTotals; }
