import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes, NavLink, Navigate } from "react-router-dom";
import "./index.css";
import { SearchPage } from "./pages/Search";
import { ItemPage } from "./pages/Item";
import { LeaderboardPage } from "./pages/Leaderboard";
import { ChannelsPage } from "./pages/Channels";
import { PredictionsPage } from "./pages/Predictions";

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
          <Routes>
            <Route path="/" element={<Navigate to="/search" replace />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/items/:id" element={<ItemPage />} />
            <Route path="/channels" element={<ChannelsPage />} />
            <Route path="/predictions" element={<PredictionsPage />} />
            <Route path="/leaderboard" element={<LeaderboardPage />} />
          </Routes>
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
