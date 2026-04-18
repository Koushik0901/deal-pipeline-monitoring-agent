# Design System Strategy: The Precision Pipeline

## 1. Overview & Creative North Star
This design system is engineered for the high-stakes environment of deal flow management. We are moving away from the "generic SaaS" look toward a concept we call **"The Quiet Architect."** 

The "Quiet Architect" aesthetic prioritizes information density and structural integrity over decorative flair. Inspired by the precision of tools like Linear and Height, the system uses a high-contrast, monochromatic foundation punctuated by a singular, authoritative "Hiive Green." The goal is to transform a complex data monitor into a scannable, editorial-grade workspace where the UI recedes to let the deals take center stage.

We achieve this through:
*   **Intentional Asymmetry:** Using varying column widths and grouped data clusters to guide the eye.
*   **Micro-Precision:** 0.5px strokes and tight radii that suggest technical rigor.
*   **Load-Bearing Typography:** Using font weight and scale as the primary navigational markers rather than icons or colors.

---

## 2. Colors & Surface Logic
Our palette is anchored in deep forest greens and a sophisticated neutral scale. The use of color is strictly functional, never decorative.

### The "No-Shadow" Depth Model
In accordance with our architectural constraints, depth is achieved through **Tonal Layering** rather than drop shadows. We use the `surface-container` tokens to "nest" information.
*   **Base:** `surface` (#f8f9fa) acts as the canvas.
*   **Sections:** Use `surface-container-low` (#f3f4f5) for large organizational blocks.
*   **Active Elements:** Use `surface-container-lowest` (#ffffff) for cards or inputs to create a "lifted" effect against the gray background.

### The "Precision Stroke" Rule
We prohibit standard 1px borders. To define boundaries, use a **0.5px stroke** utilizing the `outline-variant` (#c1c8c2) at 50% opacity. This creates a "ghost border" that is visible enough to separate data but light enough to maintain a high-density feel.

### Key Tokens
*   **Primary (Hiive Green):** `#1B4332` (Action/Authority)
*   **Secondary:** `#2D6A4F` (Progress/States)
*   **Neutral:** Grays derived from `on-surface-variant` (#414844)

---

## 3. Typography: The Load-Bearing Hierarchy
We utilize **Inter** (or system-ui) exclusively. In a dense pipeline tool, typography is the most important UI element.

*   **Display/Headline:** Use `headline-sm` (1.5rem) sparingly for page titles.
*   **The "Workhorse" (Body-MD):** 0.875rem is our default. It provides the perfect balance of readability and density.
*   **Metadata (Label-SM):** 0.6875rem in `on-surface-variant`. Use this for timestamps, secondary IDs, and micro-labels.
*   **Weight as Hierarchy:** Use `Semi-Bold` (600) for deal names and `Medium` (500) for column headers. Avoid `Bold` (700+) to keep the UI from feeling "heavy."

---

## 4. Elevation & Depth
Since shadows are prohibited, we rely on **Chromacity and Contrast** to convey importance.

*   **Tonal Stacking:** Place a `surface-container-lowest` card on a `surface-container` background. The subtle shift from #f8f9fa to #ffffff provides all the elevation needed for a professional tool.
*   **The Interaction Glow:** Instead of a shadow on hover, use a subtle background shift to `surface-bright`.
*   **Glassmorphism Lite:** For floating menus (like filter selects), use `surface-container-lowest` with a 90% opacity and a `backdrop-blur` of 8px. This creates a premium, layered feel that remains "flat" in its geometry.

---

## 5. Components

### Severity Badges (The Heart of the Monitor)
Badges must be rectangular (4px radius), never pill-shaped. They use a high-contrast "Inverted Editorial" style:
*   **ESCALATE:** Text `#ba1a1a` | Background `#ffdad6` | 0.5px Border (10% `#ba1a1a`)
*   **ACT:** Text `#1B4332` | Background `#c1ecd4` | 0.5px Border (10% `#1B4332`)
*   **WATCH:** Text `#401b1b` | Background `#ffdad8` | 0.5px Border (10% `#401b1b`)
*   **INFORMATIONAL:** Text `#414844` | Background `#edeeef` | 0.5px Border (10% `#414844`)

### Buttons
*   **Primary:** Background `#1B4332`, Text `#ffffff`. No border.
*   **Secondary:** Background `#ffffff`, Text `#414844`, 0.5px Border `#c1c8c2`.
*   **Sizing:** 32px height for standard actions; 28px for in-table actions.

### The Deal Table
*   **Cell Padding:** 8px vertical, 12px horizontal.
*   **Headers:** `label-sm` in all-caps with 0.05em tracking for a professional, "tabular" feel.
*   **Row States:** 
    *   *Default:* Transparent background.
    *   *Hover:* Background `#f3f4f5` (surface-container-low).
    *   *Selected:* Background `#c1ecd4` (primary-fixed) at 30% opacity.
*   **Dividers:** No horizontal lines. Use 1px of vertical white space or a subtle background shift between grouped rows.

### Input Fields
*   **Style:** Minimalist. No background fill. 0.5px border on the bottom only, or a 4-sided ghost border.
*   **Focus State:** Border color shifts to `primary` (#1B4332).

---

## 6. Motion & Cross-Document Morphing

Motion in this system is reserved for **state change and spatial continuity**. We do not animate for delight; we animate to tell the analyst that something they just did worked, or to preserve their sense of place between views.

### Cross-document View Transitions (pipeline row → deal detail)
When an analyst clicks a deal on `/pipeline` or in the Brief, the severity badge and deal ID morph into the destination page header rather than cross-fading with the rest of the document. This is implemented with the native View Transitions API — no JS library — using three coordinated pieces:

1. **Opt-in at the top of `input.css`** (NOT inside `@layer base {}` — Tailwind silently drops it there):
```css
@view-transition { navigation: auto; }
::view-transition-old(root) { animation-duration: 160ms; animation-timing-function: ease-in; }
::view-transition-new(root) { animation-duration: 240ms; animation-timing-function: var(--ease-out-expo); }
::view-transition-group(*)  { animation-duration: 300ms; animation-timing-function: var(--ease-out-expo); }
```

2. **Matching names on both pages** — the clicked row's badge and ID carry `view-transition-name: sev-{deal_id}` and `dealid-{deal_id}`; the detail page header carries the same names. Names must match exactly across `_macros.html` (Brief), `pipeline.html` (Pipeline), and `deal_detail.html` (detail header). Any mismatch silently falls back to a plain crossfade — no error.

3. **Reduced-motion escape hatch**:
```css
@media (prefers-reduced-motion: reduce) {
  ::view-transition-group(*), ::view-transition-old(*), ::view-transition-new(*) {
    animation-duration: 0.01ms !important; animation-delay: 0ms !important;
  }
}
```

Firefox currently ships only same-document View Transitions; cross-document is Chrome 126+ and Safari 18.2+. The feature degrades to a normal navigation everywhere else — no broken state, just no morph.

### Instant client-side filter/sort (pipeline view)
The `/pipeline` route renders **every** live deal to the DOM. Filter pills, the stage/issuer/responsible selects, the free-text search box, and column sorting are all client-side. A single global `window.PF = { filter, sort, clear }` controller toggles row visibility against `data-tier`, `data-stage`, `data-issuer`, `data-responsible`, `data-search`, `data-ratio`, `data-rofr`, `data-comm` attributes on each row.

Rules that hold this pattern together:
- **Jinja2 sets the initial `hidden` class** from URL params — this is the no-JS fallback and also prevents flash for deep-linked views.
- **`counts_by_tier` is computed before any filter** — the header always shows book-wide triage counts, not the filtered slice.
- **Keyboard navigation (`j`/`k`) reads `getVisibleRows()`** — selection respects the current filter without special-casing.
- **Sort state lives in memory only** — never pushed to the URL; sort is transient workspace state, filter is shareable context.

Analyst feedback loop: < 16ms from keystroke to re-ranked table, even with ~60 rows. That's the bar for "feels like a local tool, not a web page."

---

## 7. Future Components (Reserved for Product Expansion)

These patterns are validated for the Hiive design system and should be implemented when the corresponding product pages are built. Do not implement speculatively.

### Top Navigation Bar (full product nav)
For when multiple pages exist beyond Daily Brief:
- Height: 56px (`h-14`), sticky, bg `surface-container-lowest`, 0.5px bottom border `outline-variant/20`
- Left: Logo wordmark (`text-primary-container font-semibold tracking-tight`) + nav links
- Right: Status meta text (`text-[0.6875rem] text-on-surface-variant`) showing last tick + deal count, then date, notification/settings icon buttons, user avatar
- Nav link active state: `border-b-2 border-primary-container text-on-surface` (not a background highlight)
- Icon buttons: `hover:bg-surface-container-low p-1.5 rounded`
- User avatar: `w-8 h-8 rounded-full bg-surface-variant border-[0.5px] border-outline-variant/50`

### Pending Interventions Right-Panel (approval workflow page)
Two-column layout: 2/3 main deal table + 1/3 action cards. Useful for a dedicated approval workflow view distinct from the daily brief.
- Card: `bg-surface-container-lowest border-[0.5px] border-outline-variant/50 rounded-lg p-4 flex flex-col gap-3`
- Card header: deal meta label (`text-[0.6875rem] text-on-surface-variant`) + action title (`text-[0.875rem] font-medium text-on-surface`) + functional icon (mail, account_balance — only if actionable)
- Body: draft preview `line-clamp-2 text-[0.875rem] text-on-surface-variant`
- Actions: Approve (bg `primary-container`) + Edit (ghost border) as flex row

### "All Open Items" Expandable Footer
For paginating large intervention lists without full-page navigation:
```html
<div class="border-t-[0.5px] border-outline-variant/50 pt-4">
  <button class="flex items-center gap-2 text-[0.8125rem] font-medium text-on-surface-variant hover:text-on-surface transition-colors w-full justify-center group">
    <span>All open items (N)</span>
    <span class="group-hover:translate-y-0.5 transition-transform">↓</span>
  </button>
</div>
```

### Side Navigation Panel (multi-section product layout)
For when the product expands beyond 2-3 top-nav pages to a section-routed architecture:
- Fixed left panel, `w-64`, offset below top nav (`top-14`)
- Header: `w-10 h-10 rounded-[4px] bg-primary-container text-on-primary font-bold` monogram + product title + subtitle
- Main content must use `md:ml-64` offset class
- Nav items use **background-highlight for active state** — `bg-surface-container text-on-surface font-semibold`
- **CRITICAL**: Do NOT use `border-r-2` or `border-l-2` as the active indicator. Background tonal shift is the only allowed method (consistent with no-shadow, no-stripe rules)
- Footer: ghost "New Transaction" button + health status indicator

### Keyboard Shortcut Footer (power-user list pages)
For dense, data-heavy queue/list views used by power users (analysts processing 30-60 items/day):
- Fixed at bottom of viewport, full-width
- Styled as a minimal bar: `bg-surface-container-low/95 border-t-[0.5px] border-outline-variant/50 py-2.5`
- Content: centered row of `<kbd>` chips + label text
- Implementation: Alpine.js `@keydown.window` listeners on the page container; `htmx.trigger(btn, 'click')` for programmatic HTMX actions
- Standard intervention queue shortcuts: `j`/`k` navigate, `a` approve, `e` edit, `d` dismiss, `esc` deselect
- Only render when items exist (no empty-queue shortcuts)
- Add `pb-14` to page content to prevent footer overlap

### Breadcrumb Navigation (detail/drill-down pages)
For any page below the top-level nav (deal detail, issuer profile, etc.):
- Height: inline, sits above page heading with `mb-5`
- Font: `text-[0.6875rem] text-on-surface-variant` — same as metadata label
- Active segment (current page): `text-on-surface font-medium`
- Separator: `›` in `text-outline-variant` — not a slash, not an icon
```html
<nav class="flex items-center gap-1.5 text-[0.6875rem] text-on-surface-variant mb-5">
  <a href="/brief" class="hover:text-on-surface transition-colors">Daily Brief</a>
  <span class="text-outline-variant">›</span>
  <span class="text-on-surface font-medium">DEAL-ID</span>
  <span class="text-outline-variant">·</span>
  <span>Issuer Name</span>
</nav>
```

### Vertical Event Timeline (deal history / activity log)
For chronological event logs on detail pages. Uses CSS absolute positioning for the connecting line — no SVG required.
- Outer wrapper: `relative ml-1` — sets positioning context and offsets from left edge
- Connecting line: `absolute left-0 top-2 bottom-0 border-l-[0.5px] border-outline-variant` — half-pixel line
- Each event item: `relative pl-5 pb-5` — padding-left creates gutter for dot
- Dot: `absolute left-0 top-[0.3125rem] -translate-x-1/2 w-2 h-2 rounded-full border-[1.5px] border-surface-container-lowest` — `border-surface-container-lowest` creates knockout halo against the line
- Dot fill by event type:
  - `comm_outbound` / `comm_sent_agent_recommended` → `bg-primary-container`
  - `comm_inbound` → `bg-secondary`
  - `stage_transition` → `bg-on-surface-variant`
  - default → `bg-outline-variant`

### Deal Facts Key-Value Panel
Structured metadata sidebar for detail pages (deal size, stage, ROFR deadline, etc.):
- Uses the sidebar panel widget container (see Section 5)
- Rows separated by `border-b-[0.5px] border-outline-variant/30 last:border-b-0`
- Label: `text-[0.6875rem] text-on-surface-variant shrink-0`
- Value: `text-[0.8125rem] text-on-surface font-medium text-right`
- Implement as a Jinja2 macro (`fact_row`) for DRY templates

### 3-Column Detail Page Grid
For drill-down pages (deal detail, issuer profile) requiring structured data + analysis + history:
- Layout: `grid grid-cols-1 lg:grid-cols-4 gap-5`
  - Left 1 col: structured facts + read-only related items
  - Center 2 cols: agent analysis / primary content
  - Right 1 col: chronological timeline
- Severity rationale blocks use **background tint, not side stripe** (side stripes are banned):
  ```html
  <!-- ESCALATE rationale -->
  <p class="bg-error-container/40 border-[0.5px] border-error/20 rounded-[4px] px-3 py-2.5 text-[0.875rem] text-on-surface">...</p>
  <!-- ACT rationale -->
  <p class="bg-primary-fixed/60 border-[0.5px] border-primary-container/20 rounded-[4px] px-3 py-2.5 text-[0.875rem] text-on-surface">...</p>
  <!-- WATCH rationale -->
  <p class="bg-tertiary-fixed/40 border-[0.5px] border-tertiary/20 rounded-[4px] px-3 py-2.5 text-[0.875rem] text-on-surface">...</p>
  ```

---

## 8. Do's and Don'ts

### Do
*   **DO** use whitespace to group related deal data.
*   **DO** lean on `0.6875rem` text for secondary data to maximize screen real estate.
*   **DO** keep all corners strictly at 4px or 8px.
*   **DO** use "Hiive Green" for primary success paths and meaningful data points only.

### Don't
*   **DON'T** use icons for decoration. If an icon doesn't trigger an action or provide a critical status, remove it.
*   **DON'T** use rounded pills (stadium shapes). Everything is a soft-edged rectangle.
*   **DON'T** use 1px solid black borders. Use the 0.5px `outline-variant` token.
*   **DON'T** use drop shadows. If an element needs to stand out, change its background color or increase its stroke weight to 1px.