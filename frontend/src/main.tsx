import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes, NavLink, Navigate } from "react-router-dom";
import "./index.css";

// Route-based code splitting: each page (and its heavy deps — recharts on
// Leaderboard, react-markdown on Item) is split into its own chunk that only
// loads when the route is visited. This keeps the initial Search bundle small.
const SearchPage = lazy(() => import("./pages/Search").then(m => ({ default: m.SearchPage })));
const ItemPage = lazy(() => import("./pages/Item").then(m => ({ default: m.ItemPage })));
const LeaderboardPage = lazy(() => import("./pages/Leaderboard").then(m => ({ default: m.LeaderboardPage })));
const ChannelsPage = lazy(() => import("./pages/Channels").then(m => ({ default: m.ChannelsPage })));
const PredictionsPage = lazy(() => import("./pages/Predictions").then(m => ({ default: m.PredictionsPage })));

function Shell() {
  const linkCls = ({ isActive }: { isActive: boolean }) =>
    "px-3 py-2 rounded " + (isActive ? "bg-panel text-accent" : "hover:bg-panel");
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border bg-panel/60 backdrop-blur">
        <div className="max-w-6xl mx-auto px-4 py-3 flex gap-2 items-center">
          <div className="font-semibold text-accent mr-4">KB</div>
          <nav className="flex gap-1 text-sm">
            <NavLink to="/search" className={linkCls}>Search</NavLink>
            <NavLink to="/channels" className={linkCls}>Channels</NavLink>
            <NavLink to="/predictions" className={linkCls}>Predictions</NavLink>
            <NavLink to="/leaderboard" className={linkCls}>Leaderboard</NavLink>
          </nav>
        </div>
      </header>
      <main className="flex-1">
        <div className="max-w-6xl mx-auto px-4 py-6">
          <Suspense fallback={<div className="text-mute">Loading…</div>}>
            <Routes>
              <Route path="/" element={<Navigate to="/search" replace />} />
              <Route path="/search" element={<SearchPage />} />
              <Route path="/items/:id" element={<ItemPage />} />
              <Route path="/channels" element={<ChannelsPage />} />
              <Route path="/predictions" element={<PredictionsPage />} />
              <Route path="/leaderboard" element={<LeaderboardPage />} />
            </Routes>
          </Suspense>
        </div>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter><Shell /></BrowserRouter>
  </React.StrictMode>
);