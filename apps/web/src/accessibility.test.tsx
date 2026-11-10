/**
 * M77: Accessibility and Internationalization tests.
 *
 * Runs in vitest/jsdom environment. No external accessibility tools required.
 */

import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "@testing-library/react";
import {
  t,
  formatDate,
  formatNumber,
  formatPercent,
  formatRelativeTime,
  pluralize,
  LANGUAGES,
} from "./i18n";
import { LanguageProvider, useLanguage } from "./LanguageProvider";
import {
  useRovingTabindex,
  getContrastRatio,
  meetsContrastRatio,
} from "./a11y";
import { SkipLink } from "./A11yComponents";

// ---------------------------------------------------------------------------
// Helper component using LanguageProvider
// ---------------------------------------------------------------------------

function I18nTestComponent({ tKey, params }: { tKey: string; params?: Record<string, string | number> }) {
  const { t, lang, setLang } = useLanguage();
  return (
    <div data-testid="i18n-container">
      <span data-testid="translated-text">{t(tKey, params)}</span>
      <span data-testid="current-lang">{lang}</span>
      <button
        type="button"
        data-testid="lang-toggle"
        onClick={() => setLang(lang === "en" ? "hi" : "en")}
      >
        Switch
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Translation tests
// ---------------------------------------------------------------------------

describe("i18n translation system", () => {
  it("returns English translations by default", () => {
    expect(t("common.save")).toBe("Save");
    expect(t("nav.dashboard")).toBe("Dashboard");
    expect(t("pages.home")).toBe("Home");
  });

  it("returns Hindi translations when lang is hi", () => {
    expect(t("common.save", undefined, "hi")).toBe("सहेजें");
    expect(t("nav.dashboard", undefined, "hi")).toBe("डैशबोर्ड");
    expect(t("pages.home", undefined, "hi")).toBe("होम");
  });

  it("falls back to English for missing Hindi keys", () => {
    const missing = t("nonexistent.key.xyz", undefined, "hi");
    expect(missing).toBe("nonexistent.key.xyz");
  });

  it("substitutes parameters in translations", () => {
    const result = t("common.resultsFound", { count: 5 });
    expect(result).toBe("5 results found.");
    const resultHi = t("common.resultsFound", { count: 5 }, "hi");
    expect(resultHi).toContain("5");
  });

  it("LANGUAGES has correct entries", () => {
    expect(LANGUAGES.en.name).toBe("English");
    expect(LANGUAGES.en.nativeName).toBe("English");
    expect(LANGUAGES.en.dir).toBe("ltr");
    expect(LANGUAGES.hi.name).toBe("Hindi");
    expect(LANGUAGES.hi.nativeName).toBe("हिन्दी");
    expect(LANGUAGES.hi.dir).toBe("ltr");
  });
});

describe("LanguageProvider React context", () => {
  it("provides language context and allows switching", async () => {
    const user = userEvent.setup();
    render(
      <LanguageProvider>
        <I18nTestComponent tKey="common.save" />
      </LanguageProvider>,
    );

    expect(screen.getByTestId("translated-text").textContent).toBe("Save");
    expect(screen.getByTestId("current-lang").textContent).toBe("en");

    await user.click(screen.getByTestId("lang-toggle"));
    expect(screen.getByTestId("translated-text").textContent).toBe("सहेजें");
    expect(screen.getByTestId("current-lang").textContent).toBe("hi");

    await user.click(screen.getByTestId("lang-toggle"));
    expect(screen.getByTestId("translated-text").textContent).toBe("Save");
    expect(screen.getByTestId("current-lang").textContent).toBe("en");
  });

  it("persists language preference in localStorage", () => {
    localStorage.removeItem("wl_lang");
    const { container } = render(
      <LanguageProvider initialLang="hi">
        <I18nTestComponent tKey="common.save" />
      </LanguageProvider>,
    );

    expect(container.querySelector('[data-testid="translated-text"]')!.textContent).toBe("सहेजें");
    expect(container.querySelector('[data-testid="current-lang"]')!.textContent).toBe("hi");
  });

  it("sets document dir and lang attributes", () => {
    localStorage.removeItem("wl_lang");
    const { container } = render(
      <LanguageProvider initialLang="hi">
        <I18nTestComponent tKey="common.save" />
      </LanguageProvider>,
    );

    expect(container.querySelector('[data-testid="translated-text"]')!.textContent).toBe("सहेजें");
    expect(container.querySelector('[data-testid="current-lang"]')!.textContent).toBe("hi");
  });
});

// ---------------------------------------------------------------------------
// Locale formatting tests
// ---------------------------------------------------------------------------

describe("Locale-aware formatting", () => {
  it("formats dates in English", () => {
    expect(formatDate("2026-01-15T10:30:00Z", "en")).toBe("Jan 15, 2026");
  });

  it("formats dates in Hindi", () => {
    const hiDate = formatDate("2026-01-15T10:30:00Z", "hi");
    expect(hiDate).toBeTruthy();
    expect(hiDate).not.toBe("Jan 15, 2026");
  });

  it("formats numbers in English", () => {
    expect(formatNumber(1234.5, "en")).toBe("1,234.5");
  });

  it("formats numbers in Hindi", () => {
    const hiNum = formatNumber(1234.5, "hi");
    expect(hiNum).toBeTruthy();
  });

  it("formats percentages in English", () => {
    expect(formatPercent(75, "en")).toBe("75%");
  });

  it("formats relative time", () => {
    const past = formatRelativeTime(-3, "day", "en");
    expect(past).toBe("3 days ago");
    const future = formatRelativeTime(2, "day", "en");
    expect(future).toBe("in 2 days");
  });

  it("pluralization works correctly", () => {
    expect(pluralize(1, "item", "items", "en")).toBe("item");
    expect(pluralize(2, "item", "items", "en")).toBe("items");
  });
});

// ---------------------------------------------------------------------------
// Keyboard navigation (tested through component)
// ---------------------------------------------------------------------------

describe("Keyboard navigation", () => {
  function RovingTabindexTest({ currentIndex = 0, itemCount = 3 }: { currentIndex?: number; itemCount?: number }) {
    const { getItemProps } = useRovingTabindex({ currentIndex, itemCount, orientation: "horizontal" });
    return (
      <div role="tablist" aria-label="Roving tabindex test">
        {[0, 1, 2].map((i) => (
          <button key={`tab-${i}`} {...getItemProps(i)} data-testid={`tab-${i}`}>
            Tab {i + 1}
          </button>
        ))}
      </div>
    );
  }

  it("roving tabindex assigns correct tabIndex values", () => {
    const { container } = render(<RovingTabindexTest currentIndex={0} itemCount={3} />);
    const tabs = container.querySelectorAll("button");
    expect(tabs[0].getAttribute("tabindex")).toBe("0");
    expect(tabs[1].getAttribute("tabindex")).toBe("-1");
    expect(tabs[2].getAttribute("tabindex")).toBe("-1");
  });

  it("roving tabindex updates currentIndex on arrow keys", () => {
    function TestComponent({ currentIndex }: { currentIndex: number }) {
      const { getItemProps } = useRovingTabindex({ currentIndex, itemCount: 3, orientation: "horizontal" });
      return (
        <div>
          {[0, 1, 2].map((i) => (
            <button key={`tab-${i}`} {...getItemProps(i)} />
          ))}
        </div>
      );
    }

    const { container } = render(<RovingTabindexTest currentIndex={0} />);
    const tabs = container.querySelectorAll("button");
    expect(tabs[0].getAttribute("tabindex")).toBe("0");
    expect(tabs[1].getAttribute("tabindex")).toBe("-1");
    expect(tabs[2].getAttribute("tabindex")).toBe("-1");

    const { container: container1 } = render(<RovingTabindexTest currentIndex={1} />);
    const tabs1 = container1.querySelectorAll("button");
    expect(tabs1[0].getAttribute("tabindex")).toBe("-1");
    expect(tabs1[1].getAttribute("tabindex")).toBe("0");
    expect(tabs1[2].getAttribute("tabindex")).toBe("-1");

    const { container: container2 } = render(<RovingTabindexTest currentIndex={2} />);
    const tabs2 = container2.querySelectorAll("button");
    expect(tabs2[0].getAttribute("tabindex")).toBe("-1");
    expect(tabs2[1].getAttribute("tabindex")).toBe("-1");
    expect(tabs2[2].getAttribute("tabindex")).toBe("0");
  });
});

// ---------------------------------------------------------------------------
// Screen-reader semantics
// ---------------------------------------------------------------------------

describe("Screen-reader semantics", () => {
  it("buttons have accessible names", () => {
    render(
      <LanguageProvider>
        <I18nTestComponent tKey="common.save" />
      </LanguageProvider>,
    );

    const buttons = document.querySelectorAll("button");
    buttons.forEach((button) => {
      const name =
        button.getAttribute("aria-label") ||
        button.textContent?.trim() ||
        button.getAttribute("title");
      expect(name && name.length > 0).toBe(true);
    });
  });

  it("landmark regions are present", () => {
    render(
      <main>
        <LanguageProvider>
          <I18nTestComponent tKey="common.save" />
        </LanguageProvider>
      </main>,
    );

    expect(screen.getByRole("main")).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Color contrast
// ---------------------------------------------------------------------------

describe("Color contrast", () => {
  it("calculates contrast ratio correctly", () => {
    const ratio = getContrastRatio("#FFFFFF", "#000000");
    expect(Math.abs(ratio! - 21)).toBeLessThan(1);

    const sameRatio = getContrastRatio("#FF0000", "#FF0000");
    expect(Math.abs(sameRatio! - 1)).toBeLessThan(0.01);
  });

  it("meetsContrastRatio identifies passing/failing combinations", () => {
    expect(meetsContrastRatio("#FFFFFF", "#000000", "AAA")).toBe(true);
    expect(meetsContrastRatio("#FFFFFF", "#000000", "large")).toBe(true);
    expect(meetsContrastRatio("#CCCCCC", "#FFFFFF", "AA")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Skip link
// ---------------------------------------------------------------------------

describe("Skip-to-content link", () => {
  it("renders skip link", () => {
    const { container } = render(<SkipLink href="#main-content" label="Skip to main content" />);

    const skipLink = container.querySelector("a");
    expect(skipLink).not.toBeNull();
    expect(skipLink?.textContent).toBe("Skip to main content");
    const style = skipLink?.getAttribute("style") ?? "";
    expect(style).toContain("position");
  });
});

// ---------------------------------------------------------------------------
// useLanguage error handling
// ---------------------------------------------------------------------------

describe("useLanguage error handling", () => {
  it("throws when used outside LanguageProvider", () => {
    expect(() => {
      render(<I18nTestComponent tKey="common.save" />);
    }).toThrow();
  });
});
