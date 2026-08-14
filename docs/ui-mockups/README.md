# UI mockups (customer deck)

Open **[index.html](./index.html)** in a browser for pixel-faithful screens of the as-built Janus web app.

On this headless Spark (no `DISPLAY`), serve and open from your laptop:

```bash
cd docs/ui-mockups && python3 -m http.server 8765 --bind 0.0.0.0
# then: http://<tailscale-ip>:8765/
```

## Screenshots (JPEG)

Pre-captured at 2× for decks/slides — **[screenshots/](./screenshots/)**:

| File | Screen |
|------|--------|
| [sign-in.jpg](./screenshots/sign-in.jpg) | Create workspace |
| [chat.jpg](./screenshots/chat.jpg) | Chat with attribution |
| [models.jpg](./screenshots/models.jpg) | Model catalog |
| [model-detail.jpg](./screenshots/model-detail.jpg) | Model detail |
| [knowledge.jpg](./screenshots/knowledge.jpg) | Knowledge ingest/search |
| [agents.jpg](./screenshots/agents.jpg) | Agents create/run + citations |
| [usage.jpg](./screenshots/usage.jpg) | Usage & deployments |

| File | Role |
|------|------|
| `index.html` | Interactive gallery: Sign in → Chat → Models → Detail → Knowledge → Agents → Usage |
| `janus.css` | Copy of `apps/web/src/app/globals.css` (same tokens, layout, badges) |
| `deck.css` | Presentation chrome (browser frame, sticky tabs, action cards) |
| `screenshots/*.jpg` | JPEG captures of each tab for slides |

These are **static HTML** using the same class names and structure as `apps/web` components — not aspirational redesigns. Content is demo-filled (Acme Corp) for sales screenshots.

When you change product CSS, re-copy and re-capture:

```bash
cp apps/web/src/app/globals.css docs/ui-mockups/janus.css
cd docs/ui-mockups && python3 -m http.server 8765 --bind 0.0.0.0 &
# elsewhere, with playwright installed:
MOCKUP_URL=http://127.0.0.1:8765/index.html node capture.mjs
```
