# Dialog popups opened off-center (Framer Motion vs Tailwind transform)

_Reported 2026-07-06: "There is an issue with popup. they are not getting
opened at the center of screen." Root cause adversarially verified by a
4-dimension Workflow (transform-conflict / centering / anchored-popups /
fix-regression, 15 agents, each finding verified against the real code)
before any edit was applied._

## Summary

| # | Issue | Severity | Root cause | Fix |
|---|---|---|---|---|
| 1 | Centered `Dialog` modals render shifted down-and-right of viewport center instead of centered | **Critical (UX)** | `Dialog.jsx` centered the content with Tailwind `-translate-x-1/2 -translate-y-1/2` **and** animated the same element with Framer Motion `variants={scaleIn}` (which animates `scale`). Tailwind v3 composes `-translate-*` into the class-level `transform` (via `--tw-translate-x/y` vars); Framer Motion writes the animated transform as an **inline** `style.transform`. Inline wins, so the `-50%/-50%` centering translate was dropped while `left:50% top:50%` survived → the dialog's **top-left corner** landed at viewport center and the body extended down-and-right. Permanent at rest (Framer keeps `style.transform` set after the animation), not just during the entrance. | Move the centering translate into Framer's own transform: drop the Tailwind translate classes, add `style={{ x: '-50%', y: '-50%' }}`. Framer composes `translateX(-50%) translateY(-50%) scale(...)` into the one inline transform it controls, so centering survives `hidden`/`show`/`exit`. `scaleIn` (in `Motion.jsx`) never sets `x`/`y`, so the `-50%/-50%` persists across all variants. No DOM-structure change. |
| 2 | A `Dialog` taller than the viewport was clipped (top and bottom) with no way to scroll — Radix's body scroll lock prevented the page from scrolling to reveal the lost content | **High (UX)** | The content box had no `max-h-*` and no scroll region; `overflow` defaulted to `visible`. With `top-1/2` positioning a tall box overflowed the viewport. | Add `max-h-[calc(100vh-2rem)] flex flex-col overflow-hidden` to the box; make the children region `flex-1 min-h-0 overflow-y-auto` (the scroll region); `shrink-0` on title/description/footer so only the body scrolls. Short dialogs are visually unchanged. |

Findings that were **consequences of #1** and resolve automatically now
that centering works (no separate fix needed):

- **[medium]** A wide (`size="xl"`) dialog overflowed the right edge on
  narrow viewports — caused by the missing `-translate-x-1/2`. With
  centering restored, `w-[calc(100vw-2rem)]` + `max-w-3xl` re-centers.
- **[low]** Under `dir=rtl` the dialog inherited the same horizontal
  shift (Tailwind v3's `-translate-x-1/2` is physical, so RTL was not a
  separate bug; the Framer override removed the translate regardless of
  direction). Fixed by the centering fix.

## Why not the flex-wrapper alternative

A `fixed inset-0 flex items-center justify-center` wrapper (with the
scale animation on an inner box) would also avoid the transform conflict,
but the review **refuted** it as the preferred fix: it is more invasive
(restructures the DOM) and risks the absolute `Close` button's
`right-3 top-3` anchoring and Radix's focus-on-Content behavior. The
`style={{ x, y }}` fix changes no DOM structure, so Radix focus
management, focus trap, overlay-click dismissal, body scroll lock,
Escape-to-close, AnimatePresence exit, and the Close-button anchoring
are all preserved exactly.

## Scope of the sweep

The Workflow audited **every** `motion.*` element under `frontend/src`
for a Tailwind transform-positioning utility (`-translate-*`, `scale`,
`rotate`, `skew`) combined with a Framer transform variant. **Only
`Dialog.jsx` had the conflict.** Verified clean:

- `Sheet.jsx` — `fixed right-0 top-0` (edge-anchored, **no** translate
  utility) + `slideInRight` (animates `x`); `translateX(0)` at rest is
  harmless.
- `Toaster.jsx` — the fixed `bottom-6 right-6` container is a plain
  non-motion div; the `y`/`scale` animation is on the inner toast, so
  container positioning is unaffected.
- `Tooltip.jsx` / `DropdownMenu.jsx` — Radix Popper positioning + a CSS
  keyframe `animate-fade-in` (opacity only, no transform collision).
- `Select.jsx` — native `<select>`; the `-translate-y-1/2` caret is on
  a plain (non-motion) element.
- All page-level `motion.div` entrances (`fadeUp`, `pageEnter`, etc.)
  animate `y`/`scale` but carry no Tailwind translate/scale/rotate
  positioning class on the same element, so Framer taking over
  `transform` is harmless (those transforms never carried positional
  meaning).

## Verification

- `frontend` `npm run build` → `✓ built` (the new `max-h`/`flex-col`/
  `overflow-*`/`shrink-0` classes and the Framer `style={{ x, y }}`
  compiled without error).
- Affected consumers: `DocumentManager.jsx`, `DocumentDetail.jsx`
  (confirm dialogs) and `ConnectorDialog.jsx` (`size="xl"` create/edit
  form — the tallest, which also benefits from the new scroll region).

## Files changed

- `frontend/src/components/ui/Dialog.jsx` — centering translate moved
  into Framer `style`; tall-dialog `max-h` + scroll region added.