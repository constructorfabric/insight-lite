// The band colour ramp + the index→colour mapper, shared by the score panel
// (widgets/score/PersonScore.tsx) and the calibration editor (pages/CalibrateEditor.tsx)
// so the two can't drift. Both draw the same four-step scale on the same distribution.
//
// A colour per band STEP, taken from the band's position in the scale — not from its tone.
// tone says how severe a band is, and there are four bands over three tones: Strong and
// Solid are both "good". Colouring by tone therefore painted two different bands the same
// green, which put two identical dots in the distribution key and made the strip read as
// three steps instead of four. A scale needs one colour per step. All four are text-safe
// semantic tokens; --c-pr carries the step between warn and good.
//
// var(), not the generated token map: every one of these lands in a `color:` or
// `background:` inline style, where a custom property resolves and therefore FLIPS with the
// theme. The JS map holds the light palette only, so reading --bad from it painted dark mode
// with the light red — #d41e24 on the dark panel is 2.93:1, under the 3:1 that a 26px bold
// number needs. tokens.ts exists for values that leave CSS entirely (recharts writes SVG
// attributes, where var() does not resolve); an inline style is not that case.
export const BAND_RAMP = ["var(--bad)", "var(--warn)", "var(--c-pr)", "var(--good)"];

export function bandColor(i: number, n: number): string {
  if (i < 0 || n <= 0) return "var(--mut)";
  return BAND_RAMP[Math.round((i * (BAND_RAMP.length - 1)) / Math.max(1, n - 1))];
}
