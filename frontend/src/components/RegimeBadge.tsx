type Props = {
  regime: string | null | undefined;
};

const colors: Record<string, string> = {
  HIGH_VOLATILITY: "bg-coral/10 text-coral",
  TRENDING_BULL: "bg-mint/10 text-mint",
  TRENDING_BEAR: "bg-amber/20 text-ocean",
  RANGING: "bg-slate-200 text-slate-700",
};

export function RegimeBadge({ regime }: Props) {
  if (!regime) {
    return null;
  }

  return (
    <span className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ${colors[regime] ?? "bg-slate-200 text-slate-700"}`}>
      {regime.replace(/_/g, " ")}
    </span>
  );
}
