import { ThemeEngine } from "./themeEngine";
import type {
  BaselineSnapshot,
  DisclosureHit,
  NewsHit,
  SymbolSnapshot,
  ThemeCandidate,
} from "./types";

export type ThemeStreamEvent =
  | { type: "LEADER_DETECTED"; payload: ThemeCandidate }
  | { type: "THEME_UPDATED"; payload: ThemeCandidate }
  | { type: "THEME_CONFIRMED"; payload: ThemeCandidate }
  | { type: "THEME_ENDED"; payload: ThemeCandidate };

type Listener = (event: ThemeStreamEvent) => void;

export class ThemePipeline {
  private readonly engine: ThemeEngine;
  private readonly listeners = new Set<Listener>();
  private previousStates = new Map<string, ThemeCandidate["state"]>();

  constructor(engine = new ThemeEngine()) {
    this.engine = engine;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  ingestTick(snapshot: SymbolSnapshot, baseline: BaselineSnapshot): ThemeCandidate[] {
    const themes = this.engine.onTick(snapshot, baseline);
    this.emitStateChanges(themes);
    return themes;
  }

  ingestNews(themeId: string, hit: NewsHit): ThemeCandidate | null {
    const theme = this.engine.onNews(themeId, hit);
    if (theme) {
      this.emitStateChanges([theme]);
    }
    return theme;
  }

  ingestDisclosure(themeId: string, hit: DisclosureHit): ThemeCandidate | null {
    const theme = this.engine.onDisclosure(themeId, hit);
    if (theme) {
      this.emitStateChanges([theme]);
    }
    return theme;
  }

  getActiveThemes(): ThemeCandidate[] {
    return this.engine.getActiveThemes();
  }

  private emitStateChanges(themes: ThemeCandidate[]): void {
    for (const theme of themes) {
      const previousState = this.previousStates.get(theme.candidateId);
      const event = this.resolveEvent(theme, previousState);
      this.previousStates.set(theme.candidateId, theme.state);

      if (!event) {
        continue;
      }

      for (const listener of this.listeners) {
        listener(event);
      }
    }
  }

  private resolveEvent(
    theme: ThemeCandidate,
    previousState?: ThemeCandidate["state"],
  ): ThemeStreamEvent | null {
    if (!previousState) {
      return { type: "LEADER_DETECTED", payload: theme };
    }

    if (theme.state === "THEME_CONFIRMED" && previousState !== "THEME_CONFIRMED") {
      return { type: "THEME_CONFIRMED", payload: theme };
    }

    if (theme.state === "THEME_ENDED" && previousState !== "THEME_ENDED") {
      return { type: "THEME_ENDED", payload: theme };
    }

    if (theme.state !== previousState || theme.score > 0) {
      return { type: "THEME_UPDATED", payload: theme };
    }

    return null;
  }
}
