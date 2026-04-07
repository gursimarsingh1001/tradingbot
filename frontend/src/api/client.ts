export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export type IndexValue = {
  value: number;
  change: number;
  changePct: number;
};

export type Recommendation = {
  stockSymbol: string;
  strategyName: string;
  signalType: string;
  direction: string | null;
  confidenceScore: number;
  entryZoneLow: number | null;
  entryZoneHigh: number | null;
  stopLoss: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  paperTradeStatus: string;
  pnlRupees: number | null;
  pnlPct: number | null;
  patternName: string | null;
  regimeAtEntry: string | null;
  recommendationReason: string | null;
  basisPoints: string[] | null;
  explanationSections: Record<string, string[]> | null;
  productType: string | null;
  leverageMultiplier: number | null;
  capitalBlocked: number | null;
  remainingShares: number | null;
  maxHoldingDays: number | null;
  sector: string | null;
  sectorScore: number | null;
  daysToEarnings: number | null;
  eventFlags: string[] | null;
  fundamentalQualityScore: number | null;
  fundamentalHasSnapshot: boolean | null;
  fundamentalConfidence: number | null;
  financialDataSource: string | null;
};

export type PaperTrade = {
  tradeId: string;
  stockSymbol: string | null;
  strategyName: string | null;
  signalType: string | null;
  direction: string | null;
  entryPrice: number | null;
  currentPrice: number | null;
  exitPrice: number | null;
  stopLoss: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  pnlRupees: number | null;
  pnlPct: number | null;
  status: string;
  exitReason: string | null;
  targetsHit: Record<string, boolean> | null;
  entryDate: string | null;
  exitDate: string | null;
  sourceKind: string | null;
  watchlistReason: string | null;
  plannedForDate: string | null;
  productType: string | null;
  leverageMultiplier: number | null;
  capitalBlocked: number | null;
  remainingShares: number | null;
  initialShares: number | null;
  planStatus: string | null;
  maxHoldingDays: number | null;
  holdingHorizonLabel: string | null;
  daysHeld: number | null;
  daysRemaining: number | null;
  carriesForward: boolean;
};

export type EquityCurvePoint = {
  date: string;
  value: number;
};

export type PaperTradeHistory = {
  trades: PaperTrade[];
  equityCurve: EquityCurvePoint[];
};

export type WatchlistItem = {
  id: number;
  symbol: string | null;
  reason: string | null;
  watchPrice: number | null;
  signalType: string | null;
  strategy: string | null;
  direction: string | null;
  plannedTradeId: string | null;
  planStatus: string | null;
  plannedForDate: string | null;
  recommendationCount30d: number;
  workedCount30d: number;
  winRate30d: number;
  confidenceScore: number | null;
  newsPerspective: string | null;
  newsScore: number | null;
  eventFlags: string[] | null;
  basisPoints: string[] | null;
  explanationSections: Record<string, string[]> | null;
  sector: string | null;
  sectorScore: number | null;
  fundamentalQualityScore: number | null;
  fundamentalHasSnapshot: boolean | null;
  fundamentalConfidence: number | null;
  financialDataSource: string | null;
};

export type WatchlistAnnotationPoint = {
  date: string;
  value: number;
};

export type WatchlistAnnotation = {
  kind: string;
  label: string;
  color: string;
  points: WatchlistAnnotationPoint[] | null;
  value: number | null;
  breakoutPrice: number | null;
};

export type WatchlistDetail = {
  symbol: string;
  reason: string | null;
  strategy: string | null;
  signalType: string | null;
  direction: string | null;
  watchPrice: number | null;
  currentPrice: number | null;
  planStatus: string | null;
  productType: string | null;
  leverageMultiplier: number | null;
  capitalBlocked: number | null;
  maxHoldingDays: number | null;
  supportLevel: number | null;
  resistanceLevel: number | null;
  confidenceScore: number | null;
  newsPerspective: string | null;
  newsScore: number | null;
  eventFlags: string[] | null;
  basisPoints: string[] | null;
  explanationSections: Record<string, string[]> | null;
  sector: string | null;
  sectorScore: number | null;
  fundamentalQualityScore: number | null;
  fundamentalHasSnapshot: boolean | null;
  fundamentalConfidence: number | null;
  financialDataSource: string | null;
  chart: Array<{
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
  annotations: WatchlistAnnotation[];
};

export type RecommendationDaySummary = {
  tradeDate: string;
  stockSymbol: string;
  totalRecommendations: number;
  workedRecommendations: number;
  failedRecommendations: number;
  openRecommendations: number;
  winRate: number;
  avgPnlPct: number;
};

export type StrategyUsageSummary = {
  strategyName: string;
  trades: number;
  wins: number;
  losses: number;
  openTrades: number;
  winRate: number;
  totalPnlRupees: number;
  avgPnlPct: number;
  lastUsedOn: string | null;
};

export type StockTradeDaySummary = {
  tradeDate: string;
  trades: number;
  wins: number;
  losses: number;
  openTrades: number;
  totalPnlRupees: number;
  avgPnlPct: number;
};

export type StockPaperTradeDetail = {
  stockSymbol: string;
  days: number;
  totalTrades: number;
  wins: number;
  losses: number;
  openTrades: number;
  winRate: number;
  totalPnlRupees: number;
  avgPnlPct: number;
  bestStrategy: string | null;
  strategies: StrategyUsageSummary[];
  dailySummary: StockTradeDaySummary[];
  trades: PaperTrade[];
};

export type PaperTradeObservation = {
  days: number;
  executedTrades: number;
  openTrades: number;
  plannedTrades: number;
  wins: number;
  losses: number;
  winRate: number;
  totalPnlRupees: number;
  avgWinPct: number | null;
  avgLossPct: number | null;
  profitFactor: number | null;
  currentStreakType: string | null;
  currentStreakCount: number;
  bestStrategy: string | null;
  bestStrategyWinRate: number | null;
  portfolioValue: number;
  intradayBaseBudget: number;
  investmentBaseBudget: number;
  intradayBudget: number;
  investmentBudget: number;
  intradayBookPnlRupees: number;
  investmentBookPnlRupees: number;
  intradayOpenCapitalBlocked: number;
  intradayPlannedCapitalBlocked: number;
  investmentOpenCapitalBlocked: number;
  investmentPlannedCapitalBlocked: number;
  intradayAvailableCapital: number;
  investmentAvailableCapital: number;
};

export type BacktestStockRow = {
  symbol: string;
  bestStrategy: string | null;
  compositeScore: number | null;
  sharpeRatio: number | null;
  winRate: number | null;
  maxDrawdown: number | null;
  totalReturn: number | null;
};

export type StrategyComparison = {
  strategyName: string;
  avgSharpeRatio: number;
};

export type BacktestSummary = {
  globalBestStrategy: string | null;
  globalBestStrategyStockCount: number;
  medianSharpeRatio: number;
  stocks: BacktestStockRow[];
  strategyComparison: StrategyComparison[];
  progress: { active: boolean; progress: number; message: string } | null;
};

export type StockStrategyMetric = {
  strategyName: string;
  trades: number;
  totalReturn: number;
  winRate: number;
  avgPnlPct: number;
};

export type BacktestStockDetail = {
  symbol: string;
  strategies: StockStrategyMetric[];
  walkForwardCurve: { date: string; equity: number }[];
};

export type NewsItem = {
  id: number;
  symbol: string;
  source: string | null;
  headline: string | null;
  bodySnippet: string | null;
  publishedAt: string | null;
  sentimentLabel: string | null;
  sentimentScore: number | null;
  sentimentConfidence: number | null;
  url: string | null;
  eventFlags?: string[] | null;
};

export type CorrelationPoint = {
  date: string;
  sentimentScore: number;
  priceChangePct: number;
};

export type NewsResponse = {
  items: NewsItem[];
  correlationSeries: CorrelationPoint[];
};

export type LearningResponse = {
  currentWeights: {
    patternWeight: number;
    maWeight: number;
    volumeWeight: number;
    newsWeight: number;
    regimeWeight: number;
    fundamentalWeight: number;
    modelAccuracy: number | null;
  } | null;
  initialWeights: {
    patternWeight: number;
    maWeight: number;
    volumeWeight: number;
    newsWeight: number;
    regimeWeight: number;
    fundamentalWeight: number;
    modelAccuracy: number | null;
  } | null;
  modelAccuracy: number;
  mistakes: {
    id: number;
    tradeId: string | null;
    stockSymbol: string | null;
    strategyName: string | null;
    conditionsAtLoss: Record<string, unknown> | null;
    adjustmentMade: string | null;
    createdAt: string | null;
  }[];
};

export type KillSwitchStatus = {
  active: boolean;
  reason: string | null;
};

export type NotificationItem = {
  id: string;
  type: string | null;
  title: string | null;
  body: string | null;
  color: string | null;
  isRead: boolean;
  relatedStock: string | null;
  createdAt: string | null;
};

export type LivePayload = {
  timestamp: string;
  indices: Record<string, IndexValue>;
  watchlistPrices: { symbol: string; ltp: number; changePct: number }[];
  signals: Array<Record<string, unknown>>;
  paperTrades: PaperTrade[];
  notifications: NotificationItem[];
  killSwitch: KillSwitchStatus;
};

export const api = {
  fetchIndices: () => apiRequest<Record<string, IndexValue>>("/api/indices"),
  fetchWatchlistPrices: () => apiRequest<Array<{ symbol: string; ltp: number; changePct: number }>>("/api/stocks/prices"),
  fetchRecommendations: () => apiRequest<Recommendation[]>("/api/recommendations/today"),
  fetchPaperTradesToday: () => apiRequest<PaperTrade[]>("/api/paper-trades/today"),
  fetchPaperTradeHistory: (days: number) => apiRequest<PaperTradeHistory>(`/api/paper-trades/history?days=${days}`),
  fetchPaperTradeEffectiveness: (days: number) => apiRequest<RecommendationDaySummary[]>(`/api/paper-trades/effectiveness?days=${days}`),
  fetchPaperTradeObservation: (days: number) => apiRequest<PaperTradeObservation>(`/api/paper-trades/observation?days=${days}`),
  fetchPaperTradeStockDetail: (symbol: string, days = 90) =>
    apiRequest<StockPaperTradeDetail>(`/api/paper-trades/stock/${symbol}?days=${days}`),
  fetchTomorrowWatchlist: () => apiRequest<WatchlistItem[]>("/api/watchlist/tomorrow"),
  fetchWatchlistDetail: (symbol: string) => apiRequest<WatchlistDetail>(`/api/watchlist/tomorrow/${symbol}`),
  fetchBacktestSummary: () => apiRequest<BacktestSummary>("/api/backtest/summary"),
  fetchBacktestStock: (symbol: string) => apiRequest<BacktestStockDetail>(`/api/backtest/stock/${symbol}`),
  runFullBacktest: (limit = 0) =>
    apiRequest<{ status: string; limit: number }>(`/api/backtest/run?limit=${limit}`, { method: "POST" }),
  fetchBacktestProgress: () => apiRequest<{ active: boolean; progress: number; message: string }>("/api/backtest/progress"),
  fetchLatestNews: (params?: { symbol?: string; sentiment?: string; days?: number }) => {
    const search = new URLSearchParams();
    if (params?.symbol) search.set("symbol", params.symbol);
    if (params?.sentiment) search.set("sentiment", params.sentiment);
    if (params?.days) search.set("days", String(params.days));
    const suffix = search.toString() ? `?${search.toString()}` : "";
    return apiRequest<NewsResponse>(`/api/news/latest${suffix}`);
  },
  fetchLearningMistakes: () => apiRequest<LearningResponse>("/api/learning/mistakes"),
  fetchKillSwitchStatus: () => apiRequest<KillSwitchStatus>("/api/kill-switch/status"),
  restartKillSwitch: (confirmed: boolean) =>
    apiRequest<KillSwitchStatus>("/api/kill-switch/restart", {
      method: "POST",
      body: JSON.stringify({ confirmed }),
    }),
  fetchNotifications: () => apiRequest<NotificationItem[]>("/api/notifications"),
  markNotificationRead: (id: string) => apiRequest<NotificationItem>(`/api/notifications/${id}/read`, { method: "POST" }),
  markAllNotificationsRead: () => apiRequest<{ updated: number }>("/api/notifications/read-all", { method: "POST" }),
};
