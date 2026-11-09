/**
 * M77: Accessibility UI components for WonderLearn.
 *
 * Provides:
 *   * SkipLink - keyboard-accessible skip-to-content link.
 */

import { useCallback, useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Skip-to-content link
// ---------------------------------------------------------------------------

export function SkipLink({ href = "#main-content", label = "Skip to main content" }: { href?: string; label?: string }) {
  return (
    <a
      href={href}
      className="skip-link"
      style={{
        position: "absolute",
        left: "-9999px",
        top: "auto",
        width: "1px",
        height: "1px",
        zIndex: 9999,
      }}
    >
      {label}
    </a>
  );
}
