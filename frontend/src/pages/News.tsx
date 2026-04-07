import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";

function formatPublishedAt(value: string | null | undefined): string {
  if (!value) {
    return "Timestamp unavailable";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function News() {
  const [symbol, setSymbol] = useState("");
  const [sentiment, setSentiment] = useState("");
  const newsQuery = useQuery({
    queryKey: ["news", symbol, sentiment],
    queryFn: () => api.fetchLatestNews({ symbol: symbol || undefined, sentiment: sentiment || undefined, days: 30 }),
    refetchInterval: 60_000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });

  return (
    <div className="space-y-6">
      <section className="panel p-6">
        <div className="flex flex-wrap items-center gap-4">
          <input
            value={symbol}
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            placeholder="Filter by stock"
            className="rounded-full border border-slate-200 bg-white px-4 py-3 text-sm text-ink"
          />
          <select
            value={sentiment}
            onChange={(event) => setSentiment(event.target.value)}
            className="rounded-full border border-slate-200 bg-white px-4 py-3 text-sm text-ink"
          >
            <option value="">All sentiment</option>
            <option value="POSITIVE">Bullish</option>
            <option value="NEGATIVE">Bearish</option>
            <option value="NEUTRAL">Neutral</option>
          </select>
          <span className="text-xs uppercase tracking-[0.18em] text-slate-400">Auto-refresh every 1 minute</span>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="space-y-4">
          {newsQuery.isLoading ? <div className="panel p-6 text-sm text-slate-300">Loading news feed...</div> : null}

          {newsQuery.isError ? (
            <div className="panel p-6 text-sm text-rose-300">News feed load nahi ho paya. Page ko ek baar refresh karo.</div>
          ) : null}

          {!newsQuery.isLoading && !newsQuery.isError && (newsQuery.data?.items?.length ?? 0) === 0 ? (
            <div className="panel p-6 text-sm text-slate-300">Abhi is filter ke liye koi relevant news stored nahi hai.</div>
          ) : null}

          {newsQuery.data?.items.map((item) => (
            <article key={item.id} className="panel p-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                    {item.source} • {item.symbol}
                  </p>
                  <h3 className="mt-2 font-display text-2xl font-semibold text-ink">{item.headline}</h3>
                </div>
                <span
                  className={`rounded-full px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] ${
                    item.sentimentLabel === "POSITIVE"
                      ? "border border-[#00FFB2]/20 bg-mint/10 text-[#00FFB2] shadow-[0_0_20px_rgba(0,255,178,0.15)]"
                      : item.sentimentLabel === "NEGATIVE"
                        ? "border border-[#FF2E5B]/20 bg-coral/10 text-[#FF2E5B] shadow-[0_0_20px_rgba(255,46,91,0.15)]"
                        : "border border-violet/20 bg-violet/10 text-[#a78bfa] shadow-[0_0_20px_rgba(139,92,246,0.12)]"
                  }`}
                >
                  {item.sentimentLabel ?? "UNKNOWN"}
                </span>
              </div>
              <p className="mt-4 text-sm leading-6 text-slate-600">{item.bodySnippet}</p>
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500">
                <span>Confidence {(item.sentimentConfidence ?? 0).toFixed(2)}</span>
                <span>{formatPublishedAt(item.publishedAt)}</span>
              </div>
              {item.eventFlags?.length ? (
                <div className="mt-4 flex flex-wrap gap-2">
                  {item.eventFlags.map((flag) => (
                    <span key={flag} className="rounded-full bg-amber/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em] text-amber">
                      {flag}
                    </span>
                  ))}
                </div>
              ) : null}
            </article>
          ))}
        </div>

        <div className="panel p-6">
          <h2 className="section-title">30-Day Sentiment vs Price</h2>
          <div className="mt-4 h-[400px]">
            {(newsQuery.data?.correlationSeries?.length ?? 0) > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={newsQuery.data?.correlationSeries ?? []}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: "#475569", fontSize: 12 }} />
                  <YAxis tick={{ fill: "#475569", fontSize: 12 }} />
                  <Tooltip />
                  <Line dataKey="sentimentScore" stroke="#00FFB2" strokeWidth={2.5} dot={false} />
                  <Line dataKey="priceChangePct" stroke="#3B82F6" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-slate-400">
                Correlation chart tab dikhega jab selected stock ke liye dated news aur price data dono mil jayenge.
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
