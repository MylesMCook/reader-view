# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Reader View is a browser extension that strips clutter from webpages for clean reading, bringing Mozilla's Readability to Chromium and Firefox. This is a **fork** of [rNeomy/reader-view](https://github.com/rNeomy/reader-view) used from the Chrome Web Store — the upstream extension code is **read-only reference**.

## Critical Rule

**Do NOT modify any files outside `my-settings/`.** The `v2/` and `v3/` directories are the upstream extension source. Only `my-settings/` contains user customizations.

## No Build System

There is no build, test, or lint infrastructure. The extension runs directly from source. To test changes in `my-settings/`, paste the file contents into the extension's options page (`chrome-extension://<id>/data/options/index.html`) and save.

## Directory Structure

- **`v3/`** — Active Manifest V3 extension (read-only reference)
- **`v2/`** — Legacy Manifest V2 (read-only reference)
- **`v1/`** — Deprecated Firefox SDK version (ignore)
- **`my-settings/`** — User customizations (the only editable directory)

## my-settings/

These files are pasted into the extension options page manually:

| File | Options textarea | Target in extension |
|---|---|---|
| `reader-view.css` | "Custom styling (reader view)" | `<style id="user-css">` in `template.html` (article iframe) |
| `frame-sidebar.css` | "Custom styling (top frame and sidebar)" | `<style>` in `index.html` (toolbar/sidebar) |
| `user-action.json` | "User actions" | Custom toolbar buttons |

### user-action.json format

The `code` field **must be a single string** — the extension passes it directly to `sval` (a JS interpreter sandbox) via `instance.run(action.code)`. JSON arrays or multi-line formats will not work without modifying extension code.

The sandbox imports `document` as `iframe.contentDocument` (the reader article frame). `document.defaultView` gives access to the iframe's `window` for APIs like `IntersectionObserver`.

### sval sandbox limitations

- **Cannot override extension event handlers.** `template.js` attaches click handlers (e.g. `#reader-domain` → `top.nav.back()`) with `stopPropagation()`. Sval code cannot intercept these — `preventDefault()`, `stopImmediatePropagation()`, capturing listeners, `cloneNode()` to strip handlers, overlay divs, and `pointer-events: none` all fail to prevent the extension's handler from firing.
- **Double-quote escaping in `code` string:** Since the JSON `code` value is a double-quoted string, any embedded double quotes break parsing. Use single quotes or `%27` encoding for SVG data URIs inside `setAttribute()` calls.
- **`#reader-domain` click handler** (`template.js:32-36`): Always calls `top.nav.back(true)`. Cannot be overridden from sval. To add copy-to-clipboard, must use a separate sibling element — not the link itself.

### User-action icons

- The `icon` field renders as `<img src="...">` (see `index.js:581`), so SVG `fill='currentColor'` has no effect. Use CSS `filter` in `frame-sidebar.css` to tint icons.
- Strip explicit `width`/`height` from SVGs; keep only `viewBox` — the toolbar container sizes the `<img>`.
- Extension default `.custom img` (`index.css:556`) adds a white semi-transparent circle background + padding. Override in `frame-sidebar.css`.

## Extension Architecture (Reference)

**Dual-frame design:** Top frame (`data/reader/index.html`) has the toolbar + sidebar. An `<iframe>` loads `template.html` with the article content.

**Core flow:** User clicks icon → `worker.js` injects `Readability.js` + `wrapper.js` → article extracted → stored in IndexedDB → navigates to `data/reader/index.html?id=<tabId>` → reader fetches article and renders in iframe.

**Key reference files:**
- `v3/worker.js` — Service worker (background)
- `v3/defaults.js` — All default settings and their types
- `v3/data/reader/index.js` — Reader view logic, toolbar, user-action loading (line ~577)
- `v3/data/reader/template.js` — Article rendering inside iframe
- `v3/data/options/index.js` — Options page save/load
- `v3/data/config.js` — Config system wrapping `chrome.storage.local`

**Theming** uses CSS custom properties: `--fg`, `--bg`, `--bd`, `--lk`, `--lkv`, `--hg`. User CSS in `my-settings/` should use these for theme compatibility.

**Plugins** are ES modules in `v3/data/reader/plugins/` (tts, note, doi, chapters, qr-code, etc.), loaded dynamically via `plugins.js`.
