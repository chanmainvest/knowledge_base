import React, { Suspense, lazy, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes, NavLink, Navigate, Link } from "react-router-dom";
import "./index.css";

// Route-based code splitting: each page (and its heavy deps — recharts on
// Leaderboard/Item sparklines, react-markdown on Item) is split into its own
// chunk that only loads when the route is visited.
const SearchPage = lazy(() => import("./pages/Search").then(m => ({ default: m.SearchPage })));
const ItemPage = lazy(() => import("./pages/Item").then(m => ({ default: m.ItemPage })));
const LeaderboardPage = lazy(() => import("./pages/Leaderboard").then(m => ({ default: m.LeaderboardPage })));
const ChannelsPage = lazy(() => import("./pages/Channels").then(m => ({ default: m.ChannelsPage })));
const PredictionsPage = lazy(() => import("./pages/Predictions").then(m => ({ default: m.PredictionsPage })));
const DashboardPage = lazy(() => import("./pages/Dashboard").then(m => ({ default: m.DashboardPage })));
const InsightsPage = lazy(() => import("./pages/Insights").then(m => ({ default: m.InsightsPage })));

function NotFound() {
  return (
    <div className="border border-dashed border-border rounded p-10 text-center">
      <div className="text-3xl font-semibold text-mute">404</div>
      <div className="text-mute mt-2">That page doesn't exist.</div>
      <Link to="/dashboard" className="text-accent text-sm hover:underline mt-3 inline-block">
        Go to the dashboard →
      </Link>
    </div>
  );
}

function ThemeToggle() {
  // Dark is the default; `light` is stored in localStorage and applied by the
  // pre-paint script in index.html. Local state mirrors the class so the
  // icon updates immediately.
  const [light, setLight] = useState(
    () => !document.documentElement.classList.contains("dark"));
  function toggle() {
    const next = !light;
    setLight(next);
    document.documentElement.classList.toggle("dark", !next);
    localStorage.setItem("kb-theme", next ? "light" : "dark");
  }
  return (
    <button type="button" onClick={toggle}
      title={light ? "Switch to dark theme" : "Switch to light theme"}
      className="ml-auto px-2 py-1.5 rounded-md text-sm text-mute hover:text-ink hover:bg-panel">
      {light ? "☀" : "☾"}
    </button>
  );
}

function Shell() {
  const linkCls = ({ isActive }: { isActive: boolean }) =>
    "px-3 py-1.5 rounded-md text-sm transition-colors " +
    (isActive ? "bg-accent/15 text-accent" : "text-mute hover:text-ink hover:bg-panel");
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border bg-panel/60 backdrop-blur sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-4 py-3 flex gap-2 items-center flex-wrap">
          <Link to="/dashboard" className="flex items-center gap-2 mr-4">
            <span className="w-6 h-6 rounded bg-accent/20 border border-accent/40 text-accent
                             text-xs font-bold flex items-center justify-center">KB</span>
            <span className="font-semibold hidden sm:inline">Knowledge Base</span>
          </Link>
          <nav className="flex gap-1 flex-wrap">
            <NavLink to="/dashboard" className={linkCls}>Dashboard</NavLink>
            <NavLink to="/search" className={linkCls}>Search</NavLink>
            <NavLink to="/channels" className={linkCls}>Channels</NavLink>
            <NavLink to="/predictions" className={linkCls}>Predictions</NavLink>
            <NavLink to="/leaderboard" className={linkCls}>Leaderboard</NavLink>
            <NavLink to="/insights" className={linkCls}>Insights</NavLink>
          </nav>
          <ThemeToggle />
        </div>
      </header>
      <main className="flex-1">
        <div className="max-w-6xl mx-auto px-4 py-6">
          <Suspense fallback={
            <div className="flex items-center gap-2 text-mute py-8 justify-center">
              <span className="inline-block w-2 h-2 rounded-full bg-accent animate-pulse" />
              Loading…
            </div>
          }>
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/search" element={<SearchPage />} />
              <Route path="/items/:id" element={<ItemPage />} />
              <Route path="/channels" element={<ChannelsPage />} />
              <Route path="/predictions" element={<PredictionsPage />} />
              <Route path="/leaderboard" element={<LeaderboardPage />} />
              <Route path="/insights" element={<InsightsPage />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
        </div>
      </main>
      <footer className="border-t border-border text-xs text-mute">
        <div className="max-w-6xl mx-auto px-4 py-3 flex justify-between gap-2 flex-wrap">
          <span>chanmainvest knowledge base</span>
          <span>pipeline · extraction · scoring · market data</span>
        </div>
      </footer>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter><Shell /></BrowserRouter>
  </React.StrictMode>
);
