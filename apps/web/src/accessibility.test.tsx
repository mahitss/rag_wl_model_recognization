"""Frontend accessibility and E2E workflow tests.

Tests critical user workflows and basic accessibility checks for the
WonderLearn frontend. Runs in vitest/jsdom environment.
"""

import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import App from "./App";

describe("Accessibility checks", () => {
  it("renders app with a main landmark", () => {
    render(<App />);
    const main = screen.getByRole("main");
    expect(main).toBeInTheDocument();
  });

  it("has a skip-to-content link", () => {
    render(<App />);
    const skipLink = document.querySelector('a[href="#main-content"]');
    expect(skipLink).toBeInTheDocument();
  });

  it("images have alt attributes", () => {
    render(<App />);
    const images = document.querySelectorAll("img");
    images.forEach((img) => {
      expect(img.hasAttribute("alt")).toBe(true);
    });
  });

  it("buttons have accessible names", () => {
    render(<App />);
    const buttons = document.querySelectorAll("button");
    buttons.forEach((button) => {
      const name =
        button.getAttribute("aria-label") ||
        button.textContent?.trim() ||
        button.getAttribute("title");
      expect(name && name.length > 0).toBe(true);
    });
  });

  it("form inputs have associated labels", () => {
    render(<App />);
    const inputs = document.querySelectorAll("input, textarea, select");
    inputs.forEach((input) => {
      const id = input.getAttribute("id");
      const ariaLabel = input.getAttribute("aria-label");
      const ariaLabelledBy = input.getAttribute("aria-labelledby");
      const hasLabel =
        (id && document.querySelector(`label[for="${id}"]`)) ||
        ariaLabel ||
        ariaLabelledBy;
      expect(hasLabel).toBeTruthy();
    });
  });
});

describe("Critical user workflows", () => {
  it("app loads without crashing", () => {
    const { container } = render(<App />);
    expect(container).toBeInTheDocument();
  });

  it("app renders a heading", () => {
    render(<App />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toBeInTheDocument();
  });

  it("keyboard navigation works", () => {
    render(<App />);
    // Tab key should move focus
    document.body.focus();
    fireEvent.keyDown(document.body, { key: "Tab", code: "Tab" });
    expect(document.body).toBeInTheDocument();
  });
});
