import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { EquityCurvePoint } from "../api/client";

type Props = {
  data: EquityCurvePoint[];
};

export function EquityCurveChart({ data }: Props) {
  return (
    <div className="panel p-6">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="section-title">Equity Curve</h3>
        <span className="text-sm text-slate-500">Paper portfolio value over time</span>
      </div>
      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <XAxis dataKey="date" tick={{ fill: "#64748b", fontSize: 12 }} />
            <YAxis tick={{ fill: "#64748b", fontSize: 12 }} />
            <Tooltip />
            <Line type="monotone" dataKey="value" stroke="#134074" strokeWidth={3} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
