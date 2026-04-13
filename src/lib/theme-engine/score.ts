import type {
  NewsHit,
  SymbolSnapshot,
  ThemeBreadthEvidence,
  ThemeCandidate,
  ThemeCatalogEntry,
  ThemeFlowEvidence,
} from "./types";

export function calcBreadthEvidence(
  theme: ThemeCandidate,
  snapshots: Map<string, SymbolSnapshot>,
): ThemeBreadthEvidence {
  let risingCount = 0;
  let strongCount = 0;
  let synchronizedCount = 0;

  for (const symbol of theme.relatedSymbols) {
    const snapshot = snapshots.get(symbol);
    if (!snapshot) {
      continue;
    }

    if (snapshot.pctChange >= 2) {
      risingCount += 1;
    }
    if (snapshot.pctChange >= 5) {
      strongCount += 1;
    }
    if (snapshot.minuteReturns.m3 >= 2) {
      synchronizedCount += 1;
    }
  }

  return {
    risingCount,
    strongCount,
    synchronizedCount,
  };
}

export function calcFlowEvidence(
  theme: ThemeCandidate,
  snapshots: Map<string, SymbolSnapshot>,
  baselines: Map<string, number>,
): ThemeFlowEvidence {
  let sectorTradeValue = 0;
  let baselineTradeValue = 0;
  let leaderPersistence = 0;
  let followerSpeed = 0;

  for (const symbol of theme.relatedSymbols) {
    const snapshot = snapshots.get(symbol);
    if (!snapshot) {
      continue;
    }

    sectorTradeValue += snapshot.tradeValue;
    baselineTradeValue += baselines.get(symbol) ?? 1;

    if (symbol === theme.leader) {
      leaderPersistence =
        snapshot.pctChange >= 8 && snapshot.minuteReturns.m3 >= 0 ? 1 : 0;
    }

    if (symbol !== theme.leader && snapshot.minuteReturns.m3 >= 2) {
      followerSpeed += 1;
    }
  }

  return {
    sectorTradeValue,
    sectorTradeValueRatio:
      baselineTradeValue > 0 ? sectorTradeValue / baselineTradeValue : 0,
    leaderPersistence,
    followerSpeed,
  };
}

export function calcNewsScore(hits: NewsHit[], entry?: ThemeCatalogEntry): number {
  let score = 0;

  for (const hit of hits) {
    score += hit.commonCause ? 12 : 4;
    if (entry && hit.themeLabels.includes(entry.id)) {
      score += 8;
    }
  }

  return Math.min(score, 30);
}

export function calcDisclosureScore(commonCauseHits: number, singleCauseHits: number): number {
  const score = commonCauseHits * 8 - singleCauseHits * 4;
  return Math.max(Math.min(score, 20), -10);
}

export function calcThemeScore(input: {
  breadth: ThemeBreadthEvidence;
  flow: ThemeFlowEvidence;
  newsScore: number;
  disclosureScore: number;
}): number {
  const breadthScore =
    input.breadth.risingCount * 8 +
    input.breadth.strongCount * 12 +
    input.breadth.synchronizedCount * 6;

  const flowScore =
    Math.min(input.flow.sectorTradeValueRatio, 5) * 6 +
    input.flow.leaderPersistence * 12 +
    input.flow.followerSpeed * 5;

  return Math.max(
    0,
    Math.round(breadthScore + flowScore + input.newsScore + input.disclosureScore),
  );
}
