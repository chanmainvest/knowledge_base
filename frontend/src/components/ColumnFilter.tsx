import { useEffect, useRef, useState } from "react";

// Excel-style column header filter: a dropdown of checkboxes (one per
// distinct value in the column) plus "All"/"None" shortcuts. `selected ===
// null` means "no filter applied" (equivalent to every option checked) so
// columns start unfiltered without needing every value enumerated up front.
export interface FilterOption<T extends string | number> {
  value: T;
  label: string;
  count?: number;
}

interface ColumnFilterProps<T extends string | number> {
  options: FilterOption<T>[];
  selected: Set<T> | null;
  onChange: (next: Set<T> | null) => void;
  /** Above this many options, show a search box to narrow the checkbox list. */
  searchThreshold?: number;
}

export function ColumnFilter<T extends string | number>(
  { options, selected, onChange, searchThreshold = 8 }: ColumnFilterProps<T>,
) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocPointerDown(e: PointerEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onDocPointerDown);
    return () => document.removeEventListener("pointerdown", onDocPointerDown);
  }, [open]);

  const isActive = selected !== null && selected.size < options.length;
  const isChecked = (v: T) => selected === null || selected.has(v);
  const visible = query.trim()
    ? options.filter(o => o.label.toLowerCase().includes(query.trim().toLowerCase()))
    : options;

  // "All"/"None" apply immediately but deliberately leave the dropdown open
  // (no setOpen(false) here) so the user can fine-tune the selection right
  // after resetting it.
  function selectAll() { onChange(null); }
  function selectNone() { onChange(new Set()); }
  function toggle(v: T) {
    const next = selected === null ? new Set(options.map(o => o.value)) : new Set(selected);
    if (next.has(v)) next.delete(v); else next.add(v);
    onChange(next.size === options.length ? null : next);
  }

  return (
    <span className="relative inline-block normal-case font-normal" ref={ref}>
      <button type="button" onClick={() => setOpen(o => !o)}
        title="Filter" aria-label="Filter"
        className={"ml-1 px-1 rounded hover:bg-panel/70 " + (isActive ? "text-accent" : "text-mute")}>
        ▾
      </button>
      {open && (
        <div className="absolute z-20 top-full right-0 mt-1 w-56 bg-panel border border-border
                        rounded shadow-lg p-2 text-xs">
          {options.length > searchThreshold && (
            <input value={query} onChange={e => setQuery(e.target.value)} autoFocus
              placeholder="Search…"
              className="w-full mb-2 bg-bg border border-border rounded px-2 py-1 outline-none
                        focus:border-accent" />
          )}
          <div className="flex gap-3 mb-2">
            <button type="button" onClick={selectAll} className="text-accent hover:underline">All</button>
            <button type="button" onClick={selectNone} className="text-accent hover:underline">None</button>
          </div>
          <div className="max-h-48 overflow-y-auto space-y-1">
            {visible.map(o => (
              <label key={o.value} className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={isChecked(o.value)} onChange={() => toggle(o.value)} />
                <span className="flex-1 truncate">{o.label}</span>
                {o.count !== undefined && <span className="text-mute">{o.count}</span>}
              </label>
            ))}
            {visible.length === 0 && <div className="text-mute px-1 py-2">No matches.</div>}
          </div>
        </div>
      )}
    </span>
  );
}
