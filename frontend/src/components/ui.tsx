import { useEffect, useState } from "react";

// Current app theme, tracking the `dark` class on <html> (flipped by the
// header toggle in main.tsx). Used by pages that need theme-aware colors the
// token utilities can't express, e.g. recharts palettes.
export function useTheme(): "dark" | "light" {
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light");
  useEffect(() => {
    const obs = new MutationObserver(() =>
      setTheme(document.documentElement.classList.contains("dark") ? "dark" : "light"));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return theme;
}

// --- Shared page primitives ---------------------------------------------------
// Every page previously hand-rolled its own "Loading…" text, error string and
// empty state; these give the app one consistent set. The sortable-table
// helpers (compareValues / SortTh / useSort) were copy-pasted identically
// between Channels and Leaderboard before being extracted here.

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-mute py-8 justify-center">
      <span className="inline-block w-2 h-2 rounded-full bg-accent animate-pulse" />
      {label}
    </div>
  );
}

export function ErrorBanner({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="border border-red-500/40 bg-red-500/10 text-red-300 rounded px-3 py-2 text-sm">
      {error}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="border border-dashed border-border rounded p-8 text-center">
      <div className="text-mute">{title}</div>
      {hint && <div className="text-mute text-xs mt-2">{hint}</div>}
    </div>
  );
}

export function PageTitle({ children, sub }: { children: React.ReactNode; sub?: string }) {
  return (
    <div className="mb-4">
      <h1 className="text-xl font-semibold">{children}</h1>
      {sub && <div className="text-mute text-sm mt-0.5">{sub}</div>}
    </div>
  );
}

// Set the browser tab title for the current page (restored on unmount).
export function useTitle(title: string) {
  useEffect(() => {
    const prev = document.title;
    document.title = `${title} · KB`;
    return () => { document.title = prev; };
  }, [title]);
}

const PAGE_SIZES = [25, 50, 100, 200];

export function Pager({ page, pageSize, total, onPage, onPageSize }:
  { page: number; pageSize: number; total: number; onPage: (p: number) => void; onPageSize: (n: number) => void }) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between gap-2 flex-wrap text-sm text-mute py-2">
      <span>
        {total === 0 ? "No results." :
          `Showing ${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}`}
      </span>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2">
          Rows
          <select value={pageSize} onChange={e => onPageSize(Number(e.target.value))}
            className="bg-panel border border-border rounded px-2 py-1">
            {PAGE_SIZES.map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        {totalPages > 1 && (
          <span className="flex items-center gap-2">
            <button disabled={page <= 1} onClick={() => onPage(Math.max(1, page - 1))}
              className="border border-border rounded px-3 py-1 disabled:opacity-40 hover:border-accent">
              Prev
            </button>
            <span className="font-mono">{page}/{totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => onPage(Math.min(totalPages, page + 1))}
              className="border border-border rounded px-3 py-1 disabled:opacity-40 hover:border-accent">
              Next
            </button>
          </span>
        )}
      </div>
    </div>
  );
}

// --- Score / hit-rate formatting ----------------------------------------------

export function scoreClass(v: number | null | undefined): string {
  if (v == null) return "text-mute";
  if (v > 0) return "text-green-700 dark:text-green-400";
  if (v < 0) return "text-red-600 dark:text-red-400";
  return "";
}

export function ScoreSpan({ v, digits = 2 }: { v: number | null | undefined; digits?: number }) {
  if (v == null) return <span className="text-mute">—</span>;
  return <span className={"font-mono " + scoreClass(v)}>{v.toFixed(digits)}</span>;
}

export function HitRateSpan({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-mute">—</span>;
  return <span className="font-mono">{(v * 100).toFixed(0)}%</span>;
}

// --- Sortable tables -----------------------------------------------------------

export type SortDir = "asc" | "desc";

// Nulls always sort last, regardless of direction.
export function compareValues(a: any, b: any, dir: SortDir): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  const sign = dir === "asc" ? 1 : -1;
  return typeof a === "string" ? sign * a.localeCompare(b) : sign * (a - b);
}

export function useSort<T extends string>(initialKey: T, initialDir: SortDir = "desc",
  textKeys: Set<T> = new Set()) {
  const [sort, setSort] = useState<{ key: T; dir: SortDir }>({ key: initialKey, dir: initialDir });
  function toggleSort(key: T) {
    setSort(s => s.key === key
      ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
      : { key, dir: textKeys.has(key) ? "asc" : "desc" });
  }
  function sortRows<R>(rows: R[], keyOf: (r: R) => any): R[] {
    return [...rows].sort((a, b) => compareValues(keyOf(a), keyOf(b), sort.dir));
  }
  return { sort, toggleSort, sortRows };
}

export function SortTh({ label, active, dir, onSort, align = "left", filter }:
  { label: string; active: boolean; dir: SortDir; onSort: () => void;
    align?: "left" | "right"; filter?: React.ReactNode }) {
  return (
    <th className={"p-2 select-none whitespace-nowrap " + (align === "right" ? "text-right" : "text-left")}>
      <button type="button" onClick={onSort}
        className="inline-flex items-center gap-1 hover:text-ink">
        <span>{label}</span>
        <span className="text-[9px] text-accent w-2.5 inline-block">
          {active ? (dir === "asc" ? "▲" : "▼") : ""}
        </span>
      </button>
      {filter}
    </th>
  );
}
