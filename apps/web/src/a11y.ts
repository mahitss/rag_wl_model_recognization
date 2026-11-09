/**
 * M77: Accessibility utilities for WonderLearn.
 *
 * Provides:
 *   * Focus trap for dialogs/modals.
 *   * Visible focus indicator management.
 *   * Keyboard navigation helpers (arrow key roving tabindex).
 *   * Screen-reader live region announcements.
 *   * Color contrast validation helpers.
 *   * Reduced-motion preference detection.
 *   * Skip-to-content link rendering.
 *
 * These utilities are dependency-free and work with the existing React
 * component structure.
 */

import { useCallback, useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Focus trap
// ---------------------------------------------------------------------------

const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function useFocusTrap(active: boolean, containerRef: React.RefObject<HTMLElement>) {
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active || !containerRef.current) return;

    const container = containerRef.current;
    const focusable = Array.from(
      container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
    );
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    // Move focus into the container on mount.
    if (first) {
      previousFocus.current = document.activeElement as HTMLElement;
      first.focus({ preventScroll: true });
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      if (focusable.length === 0) return;

      if (event.shiftKey) {
        if (document.activeElement === first) {
          event.preventDefault();
          last?.focus({ preventScroll: true });
        }
      } else {
        if (document.activeElement === last || !focusable.includes(document.activeElement as HTMLElement)) {
          event.preventDefault();
          first?.focus({ preventScroll: true });
        }
      }
    };

    container.addEventListener("keydown", handleKeyDown);

    return () => {
      container.removeEventListener("keydown", handleKeyDown);
      // Restore focus on unmount if the previous element still exists.
      if (previousFocus.current && document.body.contains(previousFocus.current)) {
        previousFocus.current.focus({ preventScroll: false });
      }
    };
  }, [active, containerRef]);
}

// ---------------------------------------------------------------------------
// Visible focus indicator
// ---------------------------------------------------------------------------

let focusVisiblePolyfill = false;

export function useVisibleFocus() {
  const [isFocusVisible, setIsFocusVisible] = useState(false);

  useEffect(() => {
    const handleKeyDown = () => {
      focusVisiblePolyfill = true;
      setIsFocusVisible(true);
    };
    const handlePointerDown = () => {
      focusVisiblePolyfill = false;
      setIsFocusVisible(false);
    };

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, []);

  return isFocusVisible || focusVisiblePolyfill;
}

// ---------------------------------------------------------------------------
// Roving tabindex for keyboard navigation groups
// ---------------------------------------------------------------------------

export interface UseRovingTabindexOptions {
  currentIndex: number;
  itemCount: number;
  orientation?: "horizontal" | "vertical";
  onChange?: (index: number) => void;
  wrap?: boolean;
  disabled?: boolean;
}

export function useRovingTabindex({
  currentIndex,
  itemCount,
  orientation = "horizontal",
  onChange,
  wrap = true,
  disabled = false,
}: UseRovingTabindexOptions) {
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (disabled || itemCount === 0) return;

      let next = currentIndex;
      const isHorizontal = orientation === "horizontal";

      if (event.key === (isHorizontal ? "ArrowRight" : "ArrowDown")) {
        next = currentIndex + 1;
      } else if (event.key === (isHorizontal ? "ArrowLeft" : "ArrowUp")) {
        next = currentIndex - 1;
      } else if (event.key === "Home") {
        next = 0;
      } else if (event.key === "End") {
        next = itemCount - 1;
      } else {
        return;
      }

      if (next < 0) {
        next = wrap ? itemCount - 1 : 0;
      } else if (next >= itemCount) {
        next = wrap ? 0 : itemCount - 1;
      }

      if (next !== currentIndex && next >= 0 && next < itemCount) {
        event.preventDefault();
        onChange?.(next);
      }
    },
    [currentIndex, itemCount, orientation, onChange, wrap, disabled],
  );

  const getItemProps = (index: number) => ({
    tabIndex: index === currentIndex ? 0 : -1,
    "aria-current": index === currentIndex ? "true" : undefined,
    onKeyDown: handleKeyDown,
  });

  return { getItemProps };
}

// ---------------------------------------------------------------------------
// Live region for screen-reader announcements
// ---------------------------------------------------------------------------

export function useLiveRegion() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) {
      const el = document.createElement("div");
      el.setAttribute("aria-live", "polite");
      el.setAttribute("aria-atomic", "true");
      el.className = "sr-only";
      el.style.position = "absolute";
      el.style.width = "1px";
      el.style.height = "1px";
      el.style.padding = "0";
      el.style.margin = "-1px";
      el.style.overflow = "hidden";
      el.style.clip = "rect(0, 0, 0, 0)";
      el.style.whiteSpace = "nowrap";
      el.style.border = "0";
      document.body.appendChild(el);
      ref.current = el;
    }
    return () => {
      if (ref.current && document.body.contains(ref.current)) {
        document.body.removeChild(ref.current);
      }
    };
  }, []);

  const announce = useCallback((message: string, priority: "polite" | "assertive" = "polite") => {
    if (!ref.current) return;
    ref.current.setAttribute("aria-live", priority);
    ref.current.textContent = "";
    // Force a re-read by clearing and re-setting after a tick.
    setTimeout(() => {
      ref.current.textContent = message;
    }, 50);
  }, []);

  return { announce };
}

// ---------------------------------------------------------------------------
// Reduced motion preference
// ---------------------------------------------------------------------------

export function useReducedMotion() {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handler = (event: MediaQueryListEvent) => {
      setPrefersReducedMotion(event.matches);
    };
    setPrefersReducedMotion(media.matches);
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, []);

  return prefersReducedMotion;
}

// ---------------------------------------------------------------------------
// Color contrast helpers
// ---------------------------------------------------------------------------

function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
  const match = hex.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
  if (!match) return null;
  return {
    r: parseInt(match[1], 16),
    g: parseInt(match[2], 16),
    b: parseInt(match[3], 16),
  };
}

function getLuminance(color: string): number | null {
  const rgb = hexToRgb(color);
  if (!rgb) return null;
  const sRGB = (c: number) => {
    c = c / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * sRGB(rgb.r) + 0.7152 * sRGB(rgb.g) + 0.0722 * sRGB(rgb.b);
}

export function getContrastRatio(foreground: string, background: string): number | null {
  const lum1 = getLuminance(foreground);
  const lum2 = getLuminance(background);
  if (lum1 === null || lum2 === null) return null;
  const lighter = Math.max(lum1, lum2);
  const darker = Math.min(lum1, lum2);
  return (lighter + 0.05) / (darker + 0.05);
}

export function meetsContrastRatio(
  foreground: string,
  background: string,
  level: "AA" | "AAA" | "large" = "AA",
): boolean {
  const ratio = getContrastRatio(foreground, background);
  if (ratio === null) return false;
  if (level === "AAA") return ratio >= 7;
  if (level === "large") return ratio >= 3;
  return ratio >= 4.5;
}

// ---------------------------------------------------------------------------
// Screen-reader only visually-hidden class helper
// ---------------------------------------------------------------------------

export const srOnlyStyles: React.CSSProperties = {
  position: "absolute",
  width: "1px",
  height: "1px",
  padding: "0",
  margin: "-1px",
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: "0",
};
