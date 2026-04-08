import { useEffect, useRef, useState } from "react";
import { API_BASE_URL, LivePayload, PaperTrade } from "../api/client";

const wsBase = API_BASE_URL.replace(/^http/, "ws");

export function useWebSocket() {
  const [liveData, setLiveData] = useState<LivePayload | null>(null);
  const [connectionState, setConnectionState] = useState<"connecting" | "open" | "closed">("connecting");
  const reconnectAttempt = useRef(0);
  const lastNotificationId = useRef<string | null>(null);
  const lastMessageAt = useRef<number>(Date.now());
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let timeoutId: number | undefined;
    let watchdogId: number | undefined;
    let cancelled = false;

    const connect = () => {
      setConnectionState("connecting");
      const suffix = lastNotificationId.current ? `?lastNotificationId=${lastNotificationId.current}` : "";
      const socket = new WebSocket(`${wsBase}/ws/live${suffix}`);
      socketRef.current = socket;

      socket.onopen = () => {
        reconnectAttempt.current = 0;
        lastMessageAt.current = Date.now();
        setConnectionState("open");
      };

      socket.onmessage = (event) => {
        lastMessageAt.current = Date.now();
        const raw = JSON.parse(event.data) as Record<string, unknown>;
        const parsePaperTrade = (item: Record<string, unknown>): PaperTrade => ({
          tradeId: String(item.trade_id ?? item.tradeId ?? ""),
          stockSymbol: (item.stock_symbol as string | null | undefined) ?? (item.stockSymbol as string | null | undefined) ?? null,
          strategyName: (item.strategy_name as string | null | undefined) ?? (item.strategyName as string | null | undefined) ?? null,
          signalType: (item.signal_type as string | null | undefined) ?? (item.signalType as string | null | undefined) ?? null,
          direction: (item.direction as string | null | undefined) ?? null,
          entryPrice: item.entry_price !== undefined || item.entryPrice !== undefined ? Number(item.entry_price ?? item.entryPrice ?? 0) : null,
          currentPrice: item.current_price !== undefined || item.currentPrice !== undefined ? Number(item.current_price ?? item.currentPrice ?? 0) : null,
          exitPrice: item.exit_price !== undefined || item.exitPrice !== undefined ? Number(item.exit_price ?? item.exitPrice ?? 0) : null,
          stopLoss: item.stop_loss !== undefined || item.stopLoss !== undefined ? Number(item.stop_loss ?? item.stopLoss ?? 0) : null,
          target1: item.target_1 !== undefined || item.target1 !== undefined ? Number(item.target_1 ?? item.target1 ?? 0) : null,
          target2: item.target_2 !== undefined || item.target2 !== undefined ? Number(item.target_2 ?? item.target2 ?? 0) : null,
          target3: item.target_3 !== undefined || item.target3 !== undefined ? Number(item.target_3 ?? item.target3 ?? 0) : null,
          pnlRupees: item.pnl_rupees !== undefined || item.pnlRupees !== undefined ? Number(item.pnl_rupees ?? item.pnlRupees ?? 0) : null,
          pnlPct: item.pnl_pct !== undefined || item.pnlPct !== undefined ? Number(item.pnl_pct ?? item.pnlPct ?? 0) : null,
          status: String(item.status ?? "OPEN"),
          exitReason: (item.exit_reason as string | null | undefined) ?? (item.exitReason as string | null | undefined) ?? null,
          targetsHit: ((item.targets_hit ?? item.targetsHit) as Record<string, boolean> | null | undefined) ?? null,
          entryDate: (item.entry_date as string | null | undefined) ?? (item.entryDate as string | null | undefined) ?? null,
          exitDate: (item.exit_date as string | null | undefined) ?? (item.exitDate as string | null | undefined) ?? null,
          sourceKind: (item.source_kind as string | null | undefined) ?? (item.sourceKind as string | null | undefined) ?? null,
          watchlistReason: (item.watchlist_reason as string | null | undefined) ?? (item.watchlistReason as string | null | undefined) ?? null,
          plannedForDate: (item.planned_for_date as string | null | undefined) ?? (item.plannedForDate as string | null | undefined) ?? null,
          productType: (item.product_type as string | null | undefined) ?? (item.productType as string | null | undefined) ?? null,
          leverageMultiplier:
            item.leverage_multiplier !== undefined || item.leverageMultiplier !== undefined
              ? Number(item.leverage_multiplier ?? item.leverageMultiplier ?? 0)
              : null,
          capitalBlocked:
            item.capital_blocked !== undefined || item.capitalBlocked !== undefined
              ? Number(item.capital_blocked ?? item.capitalBlocked ?? 0)
              : null,
          remainingShares:
            item.remaining_shares !== undefined || item.remainingShares !== undefined
              ? Number(item.remaining_shares ?? item.remainingShares ?? 0)
              : null,
          initialShares:
            item.initial_shares !== undefined || item.initialShares !== undefined
              ? Number(item.initial_shares ?? item.initialShares ?? 0)
              : null,
          planStatus: (item.plan_status as string | null | undefined) ?? (item.planStatus as string | null | undefined) ?? null,
          maxHoldingDays:
            item.max_holding_days !== undefined || item.maxHoldingDays !== undefined
              ? Number(item.max_holding_days ?? item.maxHoldingDays ?? 0)
              : null,
          holdingHorizonLabel:
            (item.holding_horizon_label as string | null | undefined) ??
            (item.holdingHorizonLabel as string | null | undefined) ??
            null,
          daysHeld:
            item.days_held !== undefined || item.daysHeld !== undefined
              ? Number(item.days_held ?? item.daysHeld ?? 0)
              : null,
          daysRemaining:
            item.days_remaining !== undefined || item.daysRemaining !== undefined
              ? Number(item.days_remaining ?? item.daysRemaining ?? 0)
              : null,
          carriesForward: Boolean(item.carries_forward ?? item.carriesForward ?? false),
        });
        const payload: LivePayload = {
          timestamp: String(raw.timestamp ?? ""),
          indices: Object.fromEntries(
            Object.entries((raw.indices as Record<string, Record<string, unknown>>) ?? {}).map(([key, value]) => [
              key,
              {
                value: Number(value.value ?? 0),
                change: Number(value.change ?? 0),
                changePct: Number(value.change_pct ?? value.changePct ?? 0),
                label: (value.label as string | null | undefined) ?? null,
                source: (value.source as string | null | undefined) ?? null,
                updatedAt: (value.updated_at as string | null | undefined) ?? (value.updatedAt as string | null | undefined) ?? null,
                status: (value.status as string | null | undefined) ?? null,
                isDelayed: Boolean(value.is_delayed ?? value.isDelayed ?? false),
              },
            ]),
          ),
          watchlistPrices: ((raw.watchlist_prices ?? raw.watchlistPrices ?? []) as Array<Record<string, unknown>>).map((item) => ({
            symbol: String(item.symbol ?? ""),
            ltp: Number(item.ltp ?? item.price ?? 0),
            changePct: Number(item.change_pct ?? item.changePct ?? 0),
          })),
          signals: ((raw.signals ?? []) as Array<Record<string, unknown>>).map((item) => ({
            tradeId: item.trade_id ?? item.tradeId,
            stockSymbol: item.stock_symbol ?? item.stockSymbol,
            strategyName: item.strategy_name ?? item.strategyName,
            confidenceScore: item.confidence_score ?? item.confidenceScore,
            entryPrice: item.entry_price ?? item.entryPrice,
            entryZoneLow: item.entry_zone_low ?? item.entryZoneLow,
            entryZoneHigh: item.entry_zone_high ?? item.entryZoneHigh,
            signalType: item.signal_type ?? item.signalType,
            status: item.status,
          })),
          paperTrades: ((raw.paper_trades ?? raw.paperTrades ?? []) as Array<Record<string, unknown>>).map(parsePaperTrade),
          notifications: ((raw.notifications ?? []) as Array<Record<string, unknown>>).map((item) => ({
            id: String(item.id ?? ""),
            type: (item.type as string | null | undefined) ?? null,
            title: (item.title as string | null | undefined) ?? null,
            body: (item.body as string | null | undefined) ?? null,
            color: (item.color as string | null | undefined) ?? null,
            isRead: Boolean(item.is_read ?? item.isRead),
            relatedStock: (item.related_stock as string | null | undefined) ?? (item.relatedStock as string | null | undefined) ?? null,
            createdAt: (item.created_at as string | null | undefined) ?? (item.createdAt as string | null | undefined) ?? null,
          })),
          killSwitch: ((raw.kill_switch ?? raw.killSwitch) as { active?: boolean; reason?: string | null } | undefined)
            ? {
                active: Boolean((raw.kill_switch as { active?: boolean })?.active ?? (raw.killSwitch as { active?: boolean })?.active),
                reason:
                  ((raw.kill_switch as { reason?: string | null })?.reason ??
                    (raw.killSwitch as { reason?: string | null })?.reason ??
                    null),
              }
            : { active: false, reason: null },
        };
        const latestNotification = payload.notifications[payload.notifications.length - 1];
        if (latestNotification?.id) {
          lastNotificationId.current = latestNotification.id;
        }
        setLiveData(payload);
      };

      socket.onclose = () => {
        if (cancelled) {
          return;
        }
        setConnectionState("closed");
        setLiveData(null);
        reconnectAttempt.current += 1;
        const delay = Math.min(30000, 1000 * 2 ** reconnectAttempt.current);
        timeoutId = window.setTimeout(connect, delay);
      };

      socket.onerror = () => {
        setLiveData(null);
        socket.close();
      };
    };

    connect();
    watchdogId = window.setInterval(() => {
      const socket = socketRef.current;
      if (!socket || cancelled) {
        return;
      }
      if (socket.readyState === WebSocket.OPEN && Date.now() - lastMessageAt.current > 12000) {
        setConnectionState("closed");
        setLiveData(null);
        socket.close();
      }
    }, 4000);

    return () => {
      cancelled = true;
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
      if (watchdogId) {
        window.clearInterval(watchdogId);
      }
      socketRef.current?.close();
    };
  }, []);

  return { liveData, connectionState };
}
