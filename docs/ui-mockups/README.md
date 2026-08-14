# UI mockups (customer deck)

Open **[index.html](./index.html)** in a browser for pixel-faithful screens of the as-built Janus web app.

```bash
# from repo root
xdg-open docs/ui-mockups/index.html   # Linux
# or: open docs/ui-mockups/index.html # macOS
```

| File | Role |
|------|------|
| `index.html` | Interactive gallery: Sign in → Chat → Models → Detail → Knowledge → Agents → Usage |
| `janus.css` | Copy of `apps/web/src/app/globals.css` (same tokens, layout, badges) |
| `deck.css` | Presentation chrome (browser frame, sticky tabs, action cards) |

These are **static HTML** using the same class names and structure as `apps/web` components — not aspirational redesigns. Content is demo-filled (Acme Corp) for sales screenshots.

When you change product CSS, re-copy:

```bash
cp apps/web/src/app/globals.css docs/ui-mockups/janus.css
```
