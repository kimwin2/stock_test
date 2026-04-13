export type ThemeState =
  | "WATCHING"
  | "LEADER_DETECTED"
  | "THEME_FORMING"
  | "THEME_CONFIRMED"
  | "THEME_WEAKENING"
  | "THEME_ENDED";

export interface SymbolSnapshot {
  code: string;
  name: string;
  market?: "KOSPI" | "KOSDAQ";
  lastPrice: number;
  pctChange: number;
  volume: number;
  tradeValue: number;
  tradeStrength?: number;
  viTriggered?: boolean;
  minuteReturns: {
    m1: number;
    m3: number;
    m5: number;
  };
  updatedAt: number;
}

export interface BaselineSnapshot {
  avgTradeValue3m: number;
  avgVolume3m?: number;
}

export interface LeaderEvent {
  symbolCode: string;
  symbolName: string;
  detectedAt: number;
  triggerType: "SURGE" | "TRADE_VALUE" | "VI" | "MOMENTUM_CLUSTER";
  features: {
    pctChange: number;
    tradeValueRatio: number;
    tradeStrength?: number;
    momentum1m: number;
    momentum3m: number;
    momentum5m: number;
    viTriggered: boolean;
  };
}

export interface NewsHit {
  id: string;
  source?: string;
  publishedAt: number;
  title: string;
  summary?: string;
  url?: string;
  matchedKeywords: string[];
  themeLabels: string[];
  commonCause: boolean;
  score: number;
}

export interface DisclosureHit {
  id: string;
  publishedAt: number;
  companyCode: string;
  companyName: string;
  title: string;
  url?: string;
  category?: string;
  commonCause: boolean;
  score: number;
}

export interface ThemeBreadthEvidence {
  risingCount: number;
  strongCount: number;
  synchronizedCount: number;
}

export interface ThemeFlowEvidence {
  sectorTradeValue: number;
  sectorTradeValueRatio: number;
  leaderPersistence: number;
  followerSpeed: number;
}

export interface ThemeEvidence {
  breadth: ThemeBreadthEvidence;
  flow: ThemeFlowEvidence;
  news: {
    hits: NewsHit[];
    score: number;
  };
  disclosure: {
    hits: DisclosureHit[];
    score: number;
  };
}

export interface ThemeLabel {
  id: string;
  displayName: string;
  source: "STATIC_MAP" | "NEWS_INFERENCE" | "DYNAMIC_CLUSTER";
}

export interface ThemeCandidate {
  candidateId: string;
  label: ThemeLabel | null;
  state: ThemeState;
  leader: string;
  members: string[];
  relatedSymbols: string[];
  score: number;
  confidence: number;
  detectedAt: number;
  updatedAt: number;
  evidence: ThemeEvidence;
}

export interface ThemeCatalogEntry {
  id: string;
  displayName: string;
  symbols: string[];
  keywords: string[];
}

export interface ThemeEngineConfig {
  leader: {
    minPctChange: number;
    minTradeValueRatio: number;
    minTradeStrength: number;
    minMomentum3m: number;
  };
  follower: {
    minPctChange: number;
    minTradeValueRatio: number;
    minCountForForming: number;
    minCountForConfirmed: number;
    timeWindowMs: number;
  };
  score: {
    formingThreshold: number;
    confirmedThreshold: number;
    weakeningThreshold: number;
    endedThreshold: number;
  };
}
