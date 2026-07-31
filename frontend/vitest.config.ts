/**
 * Test runner config, kept SEPARATE from vite.config.ts — the same split
 * insight-front uses, for the same reason: the build pipeline should not pull
 * testing-library into a production bundle, and the setup file should not run
 * inside dev-mode HMR.
 *
 * Until this existed the frontend had no way to run a component at all, and the
 * guards that needed one were written as greps over the source from the Python
 * suite (tests/test_page_scripts.py). Those catch a pattern being copied; they
 * cannot catch behaviour. The first tests here are the behaviours that had none.
 *
 * jsdom rather than a browser project: lite has no Storybook and the components
 * under test are markup + state, not layout. When something needs real layout,
 * frontend/visual/ already captures screenshots against a running server.
 */
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
