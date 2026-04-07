import { X } from "lucide-react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { WatchlistDetail } from "../api/client";
import { formatInr, formatPct } from "../utils/formatters";

type Props = {
  detail: WatchlistDetail | null;
  isOpen: boolean;
  onClose: () => void;
};

function buildChartData(detail: WatchlistDetail | null) {
  if (!detail) {
    return [];
  }

  return detail.chart.map((point) => {
    const row: Record<string, number | string> = { ...point };
    detail.annotations.forEach((annotation, index) => {
      if (!annotation.points?.length) {
        return;
      }
      const match = annotation.points.find((candidate) => candidate.date === point.date);
      if (match) {
        row[`annotation_${index}`] = match.value;
      }
    });
    return row;
  });
}

export function WatchlistDetailPanel({ detail, isOpen, onClose }: Props) {
  if (!isOpen || !detail) {
    return null;
  }

  const chartData = buildChartData(detail);
  const breakoutAnnotations = detail.annotations.filter((annotation) => annotation.breakoutPrice !== null);
  const triggerGapPct =
    detail.watchPrice && detail.currentPrice
      ? (((detail.direction === "SELL" ? detail.watchPrice - detail.currentPrice : detail.currentPrice - detail.watchPrice) / detail.watchPrice) * 100)
      : null;
  const explanationSections = detail.explanationSections ?? {};
  const sectionOrder: Array<{ key: string; label: string }> = [
    { key: "technical", label: "Technical" },
    { key: "news", label: "News" },
    { key: "sector", label: "Sector" },
    { key: "fundamentals", label: "Financials" },
    { key: "risk", label: "Risk" },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-end bg-slate-950/65 backdrop-blur-sm">
      <div className="h-full w-full max-w-4xl overflow-y-auto border-l border-white/10 bg-[#09111f] p-6 text-white shadow-[0_24px_60px_rgba(2,6,23,0.55)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-slate-400">Watchlist Setup</p>
            <h3 className="mt-2 font-display text-3xl font-semibold">{detail.symbol}</h3>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">{detail.reason}</p>
          </div>
          <button
            onClick={onClose}
            className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-white/10 bg-white/5 text-slate-200 transition hover:bg-white/10"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mt-6 grid gap-4 md:grid-cols-3">
          <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Trigger / Current</p>
            <p className="mt-3 text-2xl font-semibold">{formatInr(detail.watchPrice)}</p>
            <p className="mt-1 text-sm text-slate-300">Current {formatInr(detail.currentPrice)}</p>
            <p className={`mt-2 text-sm font-semibold ${((triggerGapPct ?? 0) >= 0) ? "text-[#4be1c3]" : "text-[#ff8b5e]"}`}>
              {triggerGapPct === null ? "-" : `${triggerGapPct >= 0 ? "+" : ""}${formatPct(triggerGapPct, 2)}`}
            </p>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Strategy / Trade Rules</p>
            <p className="mt-3 text-2xl font-semibold">{detail.strategy ?? "-"}</p>
            <p className="mt-1 text-sm text-slate-300">Direction {detail.direction ?? "BUY"}</p>
            <p className="mt-1 text-sm text-slate-300">
              {detail.productType ?? "-"} {detail.leverageMultiplier ? `| ${detail.leverageMultiplier.toFixed(2)}x leverage` : ""}
            </p>
            <p className="mt-2 text-sm text-slate-300">Capital blocked {formatInr(detail.capitalBlocked)}</p>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/5 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Risk / Context</p>
            <p className="mt-3 text-2xl font-semibold">{detail.planStatus ?? "-"}</p>
            <p className="mt-1 text-sm text-slate-300">
              {detail.confidenceScore !== null && detail.confidenceScore !== undefined ? `${detail.confidenceScore.toFixed(0)} confidence` : "Confidence pending"}
            </p>
            <p className="mt-2 text-sm text-slate-300">Support {formatInr(detail.supportLevel)}</p>
            <p className="mt-1 text-sm text-slate-300">Resistance {formatInr(detail.resistanceLevel)}</p>
          </div>
        </div>

        <div className="mt-6 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="rounded-[2rem] border border-white/10 bg-white/5 p-5">
            <p className="text-sm font-semibold text-white">News Perspective</p>
            <p className="mt-3 text-sm leading-6 text-slate-300">{detail.newsPerspective ?? "No additional news context stored for this setup."}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              <span className="rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-200">
                News score {detail.newsScore !== null && detail.newsScore !== undefined ? detail.newsScore.toFixed(2) : "-"}
              </span>
              {detail.sector ? (
                <span className="rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-200">
                  {detail.sector} sector {detail.sectorScore !== null && detail.sectorScore !== undefined ? `(${detail.sectorScore.toFixed(2)})` : ""}
                </span>
              ) : null}
              {detail.fundamentalQualityScore !== null && detail.fundamentalQualityScore !== undefined ? (
                <span className="rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-200">
                  Fundamentals {detail.fundamentalQualityScore.toFixed(2)}
                </span>
              ) : null}
              {detail.financialDataSource ? (
                <span className="rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-200">
                  {detail.financialDataSource === "STRUCTURED_SNAPSHOT" ? "Structured financials" : "News-led financials"}
                </span>
              ) : null}
              {detail.fundamentalConfidence !== null && detail.fundamentalConfidence !== undefined ? (
                <span className="rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-200">
                  Financial confidence {detail.fundamentalConfidence.toFixed(2)}
                </span>
              ) : null}
              {detail.eventFlags?.map((flag) => (
                <span key={flag} className="rounded-full border border-amber/30 bg-amber/10 px-3 py-2 text-xs text-amber">
                  {flag}
                </span>
              ))}
            </div>
          </div>
          <div className="rounded-[2rem] border border-white/10 bg-white/5 p-5">
            <p className="text-sm font-semibold text-white">Setup Basis</p>
            <div className="mt-4 space-y-3">
              {sectionOrder.map((section) => {
                const points = explanationSections[section.key] ?? [];
                if (!points.length) {
                  return null;
                }
                return (
                  <div key={section.key} className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">{section.label}</p>
                    <div className="mt-2 space-y-2 text-sm leading-6 text-slate-300">
                      {points.slice(0, 3).map((point) => (
                        <p key={point}>- {point}</p>
                      ))}
                    </div>
                  </div>
                );
              })}
              {(detail.basisPoints ?? []).slice(0, 6).map((point) => (
                <div key={point} className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm leading-6 text-slate-300">
                  {point}
                </div>
              ))}
              {!(detail.basisPoints ?? []).length ? (
                <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm leading-6 text-slate-400">
                  Strategy basis will appear here when this watchlist setup carries structured notes.
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="mt-6 rounded-[2rem] border border-white/10 bg-white/5 p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-white">Chart and Setup Lines</p>
              <p className="text-xs uppercase tracking-[0.18em] text-slate-400">
                Recent price action with support, resistance, and active trendline overlays
              </p>
            </div>
            <div className="text-right text-xs text-slate-400">
              <p>{detail.strategy ?? "Strategy pending"}</p>
              <p>{detail.maxHoldingDays ? `${detail.maxHoldingDays} day holding window` : "Intraday management"}</p>
            </div>
          </div>
          {breakoutAnnotations.length ? (
            <div className="mb-4 flex flex-wrap gap-2">
              {breakoutAnnotations.map((annotation) => (
                <div key={annotation.label} className="rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-200">
                  {annotation.label} {detail.direction === "SELL" ? "breakdown" : "breakout"} near {formatInr(annotation.breakoutPrice)}
                </div>
              ))}
            </div>
          ) : null}
          <div className="h-[360px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData}>
                <CartesianGrid stroke="rgba(148,163,184,0.12)" strokeDasharray="4 4" />
                <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                <YAxis
                  yAxisId="price"
                  tick={{ fill: "#94a3b8", fontSize: 12 }}
                  tickLine={false}
                  axisLine={false}
                  domain={["dataMin - 10", "dataMax + 10"]}
                />
                <Tooltip
                  contentStyle={{ backgroundColor: "#0f172a", border: "1px solid rgba(148,163,184,0.2)", borderRadius: 16 }}
                  labelStyle={{ color: "#e2e8f0" }}
                />
                <Line yAxisId="price" type="monotone" dataKey="close" stroke="#5aa6ff" strokeWidth={3} dot={false} />
                {detail.annotations.map((annotation, index) =>
                  annotation.points?.length ? (
                    <Line
                      key={`${annotation.label}-${index}`}
                      yAxisId="price"
                      type="monotone"
                      dataKey={`annotation_${index}`}
                      stroke={annotation.color}
                      strokeWidth={2}
                      dot={false}
                      strokeDasharray="6 6"
                    />
                  ) : annotation.value !== null ? (
                    <ReferenceLine
                      key={`${annotation.label}-${index}`}
                      yAxisId="price"
                      y={annotation.value}
                      stroke={annotation.color}
                      strokeDasharray="4 4"
                      label={{ value: annotation.label, fill: annotation.color, fontSize: 11, position: "insideTopRight" }}
                    />
                  ) : null,
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}
