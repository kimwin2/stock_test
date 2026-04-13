import type {
  BaselineSnapshot,
  LeaderEvent,
  SymbolSnapshot,
  ThemeEngineConfig,
} from "./types";

export function detectLeader(
  snapshot: SymbolSnapshot,
  baseline: BaselineSnapshot,
  config: ThemeEngineConfig,
): LeaderEvent | null {
  const avgTradeValue = Math.max(baseline.avgTradeValue3m, 1);
  const tradeValueRatio = snapshot.tradeValue / avgTradeValue;
  const tradeStrength = snapshot.tradeStrength ?? 0;
  const meetsLeaderRule =
    snapshot.pctChange >= config.leader.minPctChange &&
    tradeValueRatio >= config.leader.minTradeValueRatio &&
    tradeStrength >= config.leader.minTradeStrength &&
    snapshot.minuteReturns.m3 >= config.leader.minMomentum3m;

  if (!meetsLeaderRule) {
    return null;
  }

  return {
    symbolCode: snapshot.code,
    symbolName: snapshot.name,
    detectedAt: snapshot.updatedAt,
    triggerType: snapshot.viTriggered ? "VI" : "SURGE",
    features: {
      pctChange: snapshot.pctChange,
      tradeValueRatio,
      tradeStrength: snapshot.tradeStrength,
      momentum1m: snapshot.minuteReturns.m1,
      momentum3m: snapshot.minuteReturns.m3,
      momentum5m: snapshot.minuteReturns.m5,
      viTriggered: Boolean(snapshot.viTriggered),
    },
  };
}
