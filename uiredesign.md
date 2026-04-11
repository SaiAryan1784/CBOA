# UI Redesign — Codebase Onboarding Agent

A complete visual overhaul of `static/index.html` to transform the flat GitHub-clone aesthetic into a premium, modern developer tool UI.

## Proposed Changes

### [MODIFY] [index.html](file:///Users/nuclear1784/PersonalProj/CBOA/static/index.html)

All changes are contained in this single file (self-contained HTML/CSS/JS).

#### Design System

| Token | Value |
|---|---|
| Font | `Inter` (Google Fonts) |
| Background | Deep space: `#050810` with animated star-particle canvas |
| Accent | Electric indigo → violet gradient `#6366f1 → #a855f7` |
| Glass panels | `rgba(255,255,255,0.04)` + `backdrop-filter: blur(16px)` |
| Border | `rgba(255,255,255,0.08)` subtle glows on focus/hover |
| Green success | `#10b981` |
| Red danger | `#ef4444` |
| Amber warning | `#f59e0b` |

#### Visual Upgrades

1. **Animated hero background** — floating particle/grid canvas giving a "deep space" feel
2. **Glassmorphism panels** — header, analyze bar, chat, cards all get frosted glass treatment
3. **Gradient logo & header** — `CBOA` with text gradient + glow blur shadow
4. **Animated analyze button** — shimmer/pulse effect while idle, spinner replace on click
5. **Pill-style tab bar** — rounded pill tabs with active gradient fill and smooth slide animation
6. **Progress steps** — while analyzing, show a live step tracker (Cloning → Embedding → Diagrams → Done) animated
7. **Hotspot cards** — redesigned with left color bar, file icon, commit sparkline-style bar, hover lift
8. **Vuln cards** — severity chips with glow color, expandable body
9. **Chat panel** — frosted side panel with gradient send button, typing dots animation, smooth message fade-in
10. **Empty state** — centered hero with animated icon, gradient subtitle
11. **Micro-animations** — tab switch slide, card hover lift (translateY -2px + shadow), message appear (fade+slide)
12. **Scrollbar** — styled thin indigo scrollbar
13. **Status bar** — gradient text for each state, animated progress bar pulse

#### JS Changes (minimal, same logic)

- Progress step sequencer (cosmetic, no back-end changes)
- Smooth tab switching with `transitions`
- Chat message fade-in animation via class toggle

## Verification Plan

- Open `http://localhost:8000` in browser after server starts
- Verify: header renders with gradient, background animation runs, empty state shows
- Trigger analyze → verify progress steps animate
- Check chat panel renders correctly
- Confirm all existing functionality (polling, markdown render, mermaid) still works
