/**
 * Vitest setup, loaded once per test process (see vitest.config.ts).
 *
 * Mirrors insight-front's src/test/setup.ts: jest-dom matchers so a test can say
 * `expect(node).toBeInTheDocument()`, and an unmount after each test so React
 * state does not leak from one case into the next — which matters more here than
 * usual, because some of what is under test IS module-level state that
 * deliberately outlives a component (see FilterBar's custom-range draft).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  // Every test that touches the report query starts from a clean address, since
  // the query IS the app's state (history.replaceState, not a store).
  window.history.replaceState({}, "", "/");
});
