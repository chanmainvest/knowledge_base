import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, InsightsIndex, InsightsPage as InsightsPageT } from "../api";
import { Spinner, ErrorBanner, useTitle } from "../components/ui";

// Insights tab: renders the generated llm-wiki markdown, served from disk by
// /api/insights. URL: /insights?section=People&page=adrian-day (section+page
// in query params keeps slugs simple — no nested route params needed).
export function InsightsPage() {
  const [index, setIndex] = useState<InsightsIndex | null>(null);
  const [page, setPage] = useState<InsightsPageT | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();

  const section = params.get("section");
  const pageName = params.get("page");
  useTitle(page ? page.title : "Insights");

  useEffect(() => {
    api.insightsIndex().then(setIndex).catch(e => setErr(String(e)));
  }, []);

  useEffect(() => {
    setErr(null); setPage(null);
    const req =
      section && pageName ? api.insightsPage(section, pageName)
      : section || pageName ? Promise.reject(new Error("need both section and page"))
      : api.insightsHome();
    req.then(setPage).catch(e => setErr(String(e)));
  }, [section, pageName]);

  return (
    <div className="flex flex-col lg:flex-row gap-6">
      <aside className="lg:w-56 shrink-0">
        <div className="lg:sticky lg:top-20 max-h-[75vh] overflow-y-auto pr-1">
          <button type="button"
            onClick={() => setParams({})}
            className={"block w-full text-left px-2 py-1 rounded text-sm " +
              (!section ? "bg-accent/15 text-accent" : "text-mute hover:text-ink hover:bg-panel")}>
            Overview
          </button>
          {index?.sections.map(s => (
            <div key={s.name} className="mt-3">
              <div className="px-2 text-xs font-semibold uppercase tracking-wide text-mute">
                {s.name}
              </div>
              {s.pages.map(p => {
                const active = section === s.name && pageName === p;
                return (
                  <button key={p} type="button"
                    onClick={() => setParams({ section: s.name, page: p })}
                    title={p}
                    className={"block w-full text-left px-2 py-1 rounded text-sm truncate " +
                      (active ? "bg-accent/15 text-accent" : "text-mute hover:text-ink hover:bg-panel")}>
                    {p}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </aside>
      <div className="flex-1 min-w-0">
        <ErrorBanner error={err} />
        {!page && !err && <Spinner label="Loading insights…" />}
        {page && (
          <article className="prose-kb max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{page.markdown}</ReactMarkdown>
          </article>
        )}
      </div>
    </div>
  );
}
