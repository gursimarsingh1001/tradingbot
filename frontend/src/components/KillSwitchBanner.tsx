import { AlertTriangle } from "lucide-react";
import { KillSwitchStatus } from "../api/client";

type Props = {
  status: KillSwitchStatus | undefined;
  onRestart: () => void;
};

export function KillSwitchBanner({ status, onRestart }: Props) {
  if (!status?.active) {
    return null;
  }

  return (
    <div className="mb-6 overflow-hidden rounded-[1.9rem] border border-coral/30 bg-[linear-gradient(90deg,rgba(255,125,107,0.18),rgba(255,125,107,0.06),transparent)] p-5 shadow-[0_24px_60px_rgba(255,125,107,0.18)]">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="rounded-2xl border border-coral/20 bg-coral/10 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
            <AlertTriangle className="h-6 w-6 text-coral" />
          </div>
          <div>
            <p className="font-display text-lg font-semibold text-coral">Kill switch active</p>
            <p className="text-sm text-slate-700">{status.reason}</p>
          </div>
        </div>
        <button
          onClick={onRestart}
          className="rounded-full border border-coral/30 bg-coral px-5 py-2 text-sm font-semibold text-white shadow-[0_16px_30px_rgba(255,125,107,0.24)] transition hover:brightness-110"
        >
          Restart Bot
        </button>
      </div>
    </div>
  );
}
