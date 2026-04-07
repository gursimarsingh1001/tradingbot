import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

const weightLabels = [
  { key: "patternWeight", label: "Technical Pattern" },
  { key: "maWeight", label: "Moving Averages" },
  { key: "volumeWeight", label: "Volume" },
  { key: "newsWeight", label: "News" },
  { key: "regimeWeight", label: "Regime" },
  { key: "fundamentalWeight", label: "Fundamentals" },
] as const;

export default function LearningLog() {
  const learningQuery = useQuery({ queryKey: ["learningLog"], queryFn: api.fetchLearningMistakes });
  const current = learningQuery.data?.currentWeights;
  const initial = learningQuery.data?.initialWeights;
  const weightBarClass: Record<(typeof weightLabels)[number]["key"], string> = {
    patternWeight: "bg-gradient-to-r from-[#3B82F6] to-[#8B5CF6]",
    maWeight: "bg-gradient-to-r from-[#3B82F6] to-[#06B6D4]",
    volumeWeight: "bg-gradient-to-r from-[#F59E0B] to-[#F97316]",
    newsWeight: "bg-gradient-to-r from-[#00FFB2] to-[#06B6D4]",
    regimeWeight: "bg-gradient-to-r from-[#8B5CF6] to-[#EC4899]",
    fundamentalWeight: "bg-gradient-to-r from-[#EAB308] to-[#F59E0B]",
  };

  return (
    <div className="space-y-6">
      <section className="panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="section-title">Current Scoring Weights</h2>
            <p className="mt-1 text-sm text-slate-500">Adaptive weights learned from paper-trade outcomes.</p>
          </div>
          <div className="rounded-2xl bg-slate-950 px-5 py-4 text-white">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-300">Model accuracy</p>
            <p className="mono-value mt-2 font-display text-3xl font-semibold">{((learningQuery.data?.modelAccuracy ?? 0) * 100).toFixed(2)}%</p>
          </div>
        </div>

        <div className="mt-6 space-y-5">
          {weightLabels.map(({ key, label }) => {
            const currentValue = current?.[key] ?? 0;
            const initialValue = initial?.[key] ?? 0;
            const movedUp = currentValue >= initialValue;
            return (
              <div key={key}>
                <div className="mb-2 flex items-center justify-between text-sm">
                  <span className="font-semibold text-ink">{label}</span>
                  <span className={`mono-value ${movedUp ? "pnl-up" : "pnl-down"}`}>
                    {(currentValue * 100).toFixed(1)}% {movedUp ? "↑" : "↓"} from {(initialValue * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="h-4 rounded-full bg-slate-100">
                  <div className={`h-4 rounded-full ${weightBarClass[key]}`} style={{ width: `${currentValue * 100}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="panel p-6">
        <h2 className="section-title">Mistakes Table</h2>
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.18em] text-slate-500">
              <tr>
                {["Date", "Stock", "Strategy", "Conditions", "Adjustment"].map((header) => (
                  <th key={header} className="px-3 py-3">
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {learningQuery.data?.mistakes.map((item) => (
                <tr key={item.id} className="border-t border-slate-100">
                  <td className="px-3 py-4 text-slate-500">{item.createdAt}</td>
                  <td className="px-3 py-4 font-semibold text-ink">{item.stockSymbol}</td>
                  <td className="px-3 py-4">{item.strategyName}</td>
                  <td className="px-3 py-4 text-slate-600">
                    {item.conditionsAtLoss ? JSON.stringify(item.conditionsAtLoss) : "N/A"}
                  </td>
                  <td className="px-3 py-4 text-slate-700">{item.adjustmentMade}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
