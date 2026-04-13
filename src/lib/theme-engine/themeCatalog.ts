import type { ThemeCatalogEntry } from "./types";

export const DEFAULT_THEME_CATALOG: ThemeCatalogEntry[] = [
  {
    id: "aluminum",
    displayName: "알루미늄",
    symbols: ["008350", "018470", "006110", "001780", "128660"],
    keywords: ["알루미늄", "비철금속", "원자재", "공급차질", "관세"],
  },
  {
    id: "shipbuilding",
    displayName: "조선",
    symbols: [],
    keywords: ["조선", "LNG선", "수주", "해운"],
  },
  {
    id: "secondary-battery",
    displayName: "2차전지",
    symbols: [],
    keywords: ["2차전지", "배터리", "양극재", "리튬"],
  },
];

export function findCatalogEntriesBySymbol(
  catalog: ThemeCatalogEntry[],
  symbolCode: string,
): ThemeCatalogEntry[] {
  return catalog.filter((entry) => entry.symbols.includes(symbolCode));
}

export function findCatalogEntriesByKeywords(
  catalog: ThemeCatalogEntry[],
  keywords: string[],
): ThemeCatalogEntry[] {
  return catalog.filter((entry) =>
    entry.keywords.some((keyword) => keywords.includes(keyword)),
  );
}
