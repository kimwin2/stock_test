import { detectLeader } from "./leaderDetector";
import { DEFAULT_THEME_CATALOG, findCatalogEntriesBySymbol } from "./themeCatalog";
import {
  calcBreadthEvidence,
  calcDisclosureScore,
  calcFlowEvidence,
  calcNewsScore,
  calcThemeScore,
} from "./score";
import type {
  BaselineSnapshot,
  DisclosureHit,
  LeaderEvent,
  NewsHit,
  SymbolSnapshot,
  ThemeCandidate,
  ThemeCatalogEntry,
  ThemeEngineConfig,
  ThemeLabel,
  ThemeState,
} from "./types";

const DEFAULT_CONFIG: ThemeEngineConfig = {
  leader: {
    minPctChange: 7,
    minTradeValueRatio: 4,
    minTradeStrength: 150,
    minMomentum3m: 4,
  },
  follower: {
    minPctChange: 3,
    minTradeValueRatio: 2,
    minCountForForming: 2,
    minCountForConfirmed: 3,
    timeWindowMs: 15 * 60 * 1000,
  },
  score: {
    formingThreshold: 40,
    confirmedThreshold: 70,
    weakeningThreshold: 25,
    endedThreshold: 10,
  },
};

export class ThemeEngine {
  private readonly config: ThemeEngineConfig;
  private readonly catalog: ThemeCatalogEntry[];
  private readonly snapshots = new Map<string, SymbolSnapshot>();
  private readonly baselines = new Map<string, BaselineSnapshot>();
  private readonly activeThemes = new Map<string, ThemeCandidate>();
  private readonly newsByTheme = new Map<string, NewsHit[]>();
  private readonly disclosuresByTheme = new Map<string, DisclosureHit[]>();

  constructor(
    config: Partial<ThemeEngineConfig> = {},
    catalog: ThemeCatalogEntry[] = DEFAULT_THEME_CATALOG,
  ) {
    this.catalog = catalog;
    this.config = {
      leader: { ...DEFAULT_CONFIG.leader, ...config.leader },
      follower: { ...DEFAULT_CONFIG.follower, ...config.follower },
      score: { ...DEFAULT_CONFIG.score, ...config.score },
    };
  }

  onTick(snapshot: SymbolSnapshot, baseline: BaselineSnapshot): ThemeCandidate[] {
    this.snapshots.set(snapshot.code, snapshot);
    this.baselines.set(snapshot.code, baseline);

    const leader = detectLeader(snapshot, baseline, this.config);
    if (leader) {
      this.onLeader(leader);
    }

    return this.recalculateActiveThemes(snapshot.updatedAt);
  }

  onLeader(event: LeaderEvent): ThemeCandidate[] {
    const entries = findCatalogEntriesBySymbol(this.catalog, event.symbolCode);
    const now = event.detectedAt;

    if (entries.length === 0) {
      const dynamicTheme = this.createThemeCandidate(
        `dynamic:${event.symbolCode}:${now}`,
        {
          id: `dynamic:${event.symbolCode}`,
          displayName: `${event.symbolName} 연관`,
          source: "DYNAMIC_CLUSTER",
        },
        event,
        [event.symbolCode],
      );
      this.activeThemes.set(dynamicTheme.candidateId, dynamicTheme);
      return [dynamicTheme];
    }

    const created: ThemeCandidate[] = [];

    for (const entry of entries) {
      const candidateId = `${entry.id}:${now}:${event.symbolCode}`;
      const label: ThemeLabel = {
        id: entry.id,
        displayName: entry.displayName,
        source: "STATIC_MAP",
      };
      const theme = this.createThemeCandidate(
        candidateId,
        label,
        event,
        entry.symbols.length > 0 ? entry.symbols : [event.symbolCode],
      );
      this.activeThemes.set(candidateId, theme);
      created.push(theme);
    }

    return created;
  }

  onNews(themeId: string, hit: NewsHit): ThemeCandidate | null {
    const hits = this.newsByTheme.get(themeId) ?? [];
    hits.push(hit);
    this.newsByTheme.set(themeId, hits);
    return this.refreshTheme(themeId);
  }

  onDisclosure(themeId: string, hit: DisclosureHit): ThemeCandidate | null {
    const hits = this.disclosuresByTheme.get(themeId) ?? [];
    hits.push(hit);
    this.disclosuresByTheme.set(themeId, hits);
    return this.refreshTheme(themeId);
  }

  getActiveThemes(): ThemeCandidate[] {
    return [...this.activeThemes.values()].sort((a, b) => b.score - a.score);
  }

  private refreshTheme(themeId: string): ThemeCandidate | null {
    const theme = this.activeThemes.get(themeId);
    if (!theme) {
      return null;
    }

    const refreshed = this.recalculateTheme(theme, Date.now());
    this.activeThemes.set(themeId, refreshed);
    return refreshed;
  }

  private recalculateActiveThemes(now: number): ThemeCandidate[] {
    const updated: ThemeCandidate[] = [];

    for (const [themeId, theme] of this.activeThemes.entries()) {
      const next = this.recalculateTheme(theme, now);
      this.activeThemes.set(themeId, next);
      updated.push(next);
    }

    return updated.sort((a, b) => b.score - a.score);
  }

  private recalculateTheme(theme: ThemeCandidate, now: number): ThemeCandidate {
    const breadth = calcBreadthEvidence(theme, this.snapshots);
    const baselineMap = new Map<string, number>();

    for (const symbol of theme.relatedSymbols) {
      baselineMap.set(symbol, this.baselines.get(symbol)?.avgTradeValue3m ?? 1);
    }

    const flow = calcFlowEvidence(theme, this.snapshots, baselineMap);
    const catalogEntry = theme.label
      ? this.catalog.find((entry) => entry.id === theme.label?.id)
      : undefined;
    const newsHits = this.newsByTheme.get(theme.candidateId) ?? [];
    const disclosureHits = this.disclosuresByTheme.get(theme.candidateId) ?? [];
    const newsScore = calcNewsScore(newsHits, catalogEntry);
    const disclosureScore = calcDisclosureScore(
      disclosureHits.filter((hit) => hit.commonCause).length,
      disclosureHits.filter((hit) => !hit.commonCause).length,
    );
    const score = calcThemeScore({
      breadth,
      flow,
      newsScore,
      disclosureScore,
    });
    const nextState = this.resolveState(theme, score, breadth.risingCount, now);

    return {
      ...theme,
      state: nextState,
      members: this.resolveActiveMembers(theme.relatedSymbols),
      score,
      confidence: Math.min(100, score),
      updatedAt: now,
      evidence: {
        breadth,
        flow,
        news: {
          hits: newsHits,
          score: newsScore,
        },
        disclosure: {
          hits: disclosureHits,
          score: disclosureScore,
        },
      },
    };
  }

  private resolveState(
    theme: ThemeCandidate,
    score: number,
    risingCount: number,
    now: number,
  ): ThemeState {
    const ageMs = now - theme.detectedAt;
    const stale =
      ageMs > this.config.follower.timeWindowMs &&
      risingCount < this.config.follower.minCountForForming;

    if (stale || score <= this.config.score.endedThreshold) {
      return "THEME_ENDED";
    }
    if (
      risingCount >= this.config.follower.minCountForConfirmed &&
      score >= this.config.score.confirmedThreshold
    ) {
      return "THEME_CONFIRMED";
    }
    if (
      risingCount >= this.config.follower.minCountForForming &&
      score >= this.config.score.formingThreshold
    ) {
      return "THEME_FORMING";
    }
    if (score <= this.config.score.weakeningThreshold) {
      return "THEME_WEAKENING";
    }
    return "LEADER_DETECTED";
  }

  private resolveActiveMembers(relatedSymbols: string[]): string[] {
    return relatedSymbols.filter((symbol) => {
      const snapshot = this.snapshots.get(symbol);
      return Boolean(snapshot && snapshot.pctChange >= this.config.follower.minPctChange);
    });
  }

  private createThemeCandidate(
    candidateId: string,
    label: ThemeLabel,
    leader: LeaderEvent,
    relatedSymbols: string[],
  ): ThemeCandidate {
    return {
      candidateId,
      label,
      state: "LEADER_DETECTED",
      leader: leader.symbolCode,
      members: [leader.symbolCode],
      relatedSymbols,
      score: 0,
      confidence: 0,
      detectedAt: leader.detectedAt,
      updatedAt: leader.detectedAt,
      evidence: {
        breadth: {
          risingCount: 1,
          strongCount: 1,
          synchronizedCount: 1,
        },
        flow: {
          sectorTradeValue: 0,
          sectorTradeValueRatio: 0,
          leaderPersistence: 1,
          followerSpeed: 0,
        },
        news: {
          hits: [],
          score: 0,
        },
        disclosure: {
          hits: [],
          score: 0,
        },
      },
    };
  }
}
