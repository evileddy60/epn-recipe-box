# EPN Recipe Box visual style

## Mood
Warm, tactile recipe cards: pantry paper, walnut box, tomato-red actions, herb-green success, and ink-brown text. The interface should feel calm and useful rather than decorative.

## Colour roles
- Ink `#2d241d`: primary text and headings.
- Muted `#74665a`: supporting text.
- Paper `#fff8e8`: cards and forms.
- Paper deep `#f5dfb9`: selected/secondary surfaces.
- Box `#8d4f2a`: navigation and recipe-box surfaces.
- Accent `#b13f2c`: primary actions and destructive/conflict emphasis.
- Green `#416f42`: success and healthy connection state.
- Blue `#355c7d`: informational peer state.
- Lines `#d4b274` / `#e5c991`: structure, never body text.

## Typography
- Display and recipe titles: Georgia/Times New Roman fallback for an editorial handwritten-card feel.
- Body and controls: Inter/system UI stack already used by the application.
- Use clear sentence-case labels, short helper text, and tabular numerals for counts/timestamps.

## Spacing and layout
- Base spacing unit: 8px; use 8/12/16/24/32px rhythm.
- Content width: existing 980px maximum.
- Cards use an outer radius of 8px and inner controls of 8px only when not nested; nested surfaces should be visually inset.
- Sync dashboard uses one-column mobile layout and two-column desktop layout.

## Components and states
- Buttons: 42px minimum height, clear primary/secondary treatment, visible focus ring, pressed scale no smaller than 0.96.
- Peer cards: name, URL, enabled/disabled badge, last sync, and explicit action grouping.
- Status badges: green success, blue information, amber warning, red conflict/failure; include text, not colour alone.
- Empty/loading/success/warning/conflict/failure states use a heading plus one actionable sentence.

## Responsive and accessibility rules
- Preserve semantic headings, landmarks, labels, and button elements.
- Touch targets are at least 44px where practical.
- Focus uses a 3px accent outline with offset; never remove the browser focus indication without replacement.
- Respect `prefers-reduced-motion: reduce`; no motion is required to understand sync results.
- Maintain readable contrast and allow long peer URLs to wrap.
- Do not expose tokens in rendered HTML, logs, URLs, or browser-visible error messages.
