# Wavelog plugins

Plugins add pages, desktop widgets, notch sections, alerts and in-game panels to [Wavelog](https://github.com/rockslol/wavelog). This repo holds the official plugins and `registry.json`, the list Wavelog shows in **Settings → Plugins → Get plugins**.

Plugins are written in Python, and you don't need a UI framework or a build step. Your code returns **blocks** (tiles, lists, grids, charts, buttons), and Wavelog draws them in its own style.

| Plugin | What it does |
|---|---|
| [Today](today/) | Today's listening on one page, with a widget and a notch section. Start here when you make your own: it uses every block type. |
| [Valorant](valorant/) | Rank, RR, matches and rounds, the store with a wishlist, a Tab panel in matches, and quieter Spotify while you play. |

## Make a plugin in five minutes

1. Copy the `today/` folder and give it a new `id` and `name` in `plugin.json`.
2. In Wavelog, open **Settings → Plugins → Developer mode → Load a folder** and paste your folder's path.
3. Edit `main.py`. Wavelog reloads the plugin every time you save, and errors show on its card in Settings → Plugins.

## The folder

```
my-plugin/
  plugin.json   what the plugin is, its settings, what it uses
  main.py       the code
  icon.svg      optional (svg, png, jpg, webp)
  lib/          optional: pure-Python packages it needs
```

### plugin.json

```json
{
  "id": "my-plugin",
  "name": "My plugin",
  "version": "1.0.0",
  "author": "you",
  "description": "One sentence, shown in Get plugins.",
  "api": 1,
  "min_app": "0.10.0",
  "icon": "icon.svg",
  "uses": { "network": ["api.example.com"], "plays": true },
  "settings": [
    { "key": "goal", "type": "number", "label": "Daily goal in minutes", "default": 60, "min": 1, "max": 1440 }
  ]
}
```

- `id`: 2 to 32 lowercase letters, numbers or dashes. It must never change.
- `version`: `1.2.3`. Raise it for every release, and Wavelog offers the update.
- `min_app`: the oldest Wavelog version your plugin works with.
- `settings`: drawn on the plugin's card. The types are `switch`, `text`, `number`, `slider` (`min`, `max`, `step`) and `choice` (`options: [["value", "Label"], …]`). Each one can have a `hint`. A `text` setting can also have `placeholder` and `secret`.
- `uses`: shown to people before they install. The keys are `network` (list of what it connects to), `files` (folders it reads), `plays`, `games`, `overlay` and `ducking` (true/false). It's information, not a sandbox, so be honest. Reviews check it.

### main.py

```python
from wavelog import Plugin, ui

p = Plugin()

@p.job(every=900)                       # background job, every 15 minutes
async def sync(ctx): ...

@p.page("My page", ranges=True)         # a tab in the stats window; ranges adds Today / 7 days / …
async def page(ctx, range):
    return [ui.header("Hello", "a subtitle"), ui.tiles(ui.tile("Plays", 42), ui.tile("Minutes", 120))]

@p.widget("My widget", sizes="sml")     # a desktop widget; size is "s", "m" or "l"
async def widget(ctx, size): ...

@p.notch_section("My section")          # a section in the notch's dashboard
async def section(ctx): ...

@p.overlay(game="Game-Win64-Shipping.exe", key="Tab")   # a panel while a key is held in a game
async def panel(ctx): ...

@p.duck                                 # return True while Spotify should be quieter
def busy(ctx): ...

@p.action                               # called by buttons, cards, chips and search boxes
async def refresh(ctx): ...
```

Handlers can be `async` or plain functions. Plain ones run on a worker thread, so slow code doesn't freeze anything. Each handler gets `ctx` plus only the arguments it names. Also available: `@p.on_start`, `@p.on_stop`, `@p.on_settings`.

**`ctx`**

| | |
|---|---|
| `ctx.settings` | the plugin's settings, with what the user picked |
| `ctx.set(key, value)` | save a setting, or any small value |
| `ctx.store`, `ctx.save()` | a dict kept as `store.json` in the plugin's data folder |
| `ctx.db` | the plugin's own SQLite database (autocommit) |
| `ctx.data` | the plugin's data folder. It stays when the plugin is removed, so reinstalling brings everything back |
| `ctx.http` | a shared `httpx.AsyncClient` |
| `ctx.now()` | what Spotify plays right now, or None |
| `ctx.plays(start, end)` | Spotify plays that ended between two Unix times |
| `ctx.game()` | the fullscreen game in front, `{"exe", "title"}`, or None |
| `ctx.alert(text, sub, icon, color)` | a notch alert for 20 seconds |
| `ctx.log` | a logger, written to Wavelog's `app.log` |

Plugins never get the user's Spotify login: use `ctx.now()` and `ctx.plays()`.

**Blocks** (`ui.…`): `header`, `hero`, `tiles`/`tile`, `bars`, `list`/`item`, `grid`/`card`, `chips`/`chip`, `seg` (timeline strips), `text`, `buttons`/`button`, `search` and `columns`. Images can be https links (Wavelog fetches and caches them) or files in your folder. The docstrings in [wavelog.py](https://github.com/rockslol/wavelog/blob/main/backend/wavelog.py) list every option, and `today/main.py` shows all of them in use.

### What you can import

Wavelog is one bundled program, so plugins can only import what's in it:

- **Standard library:** asyncio, base64, collections, csv, ctypes, dataclasses, datetime, hashlib, html, json, math, os, pathlib, random, re, socket, sqlite3, ssl, statistics, subprocess, threading, time, urllib, uuid, winreg, xml, zipfile, zoneinfo and more. `MODULES` in wavelog.py is the exact list.
- **Packages:** httpx, PIL (Pillow) and psutil.

Anything else must be pure Python and ship in your plugin's `lib/` folder.

## Get your plugin listed

1. Keep your plugin in your own public GitHub repo.
2. Make a zip of the plugin folder and attach it to a GitHub release there. To get the same zip every time, run `python tools/pack.py <your folder>` from a copy of this repo; it prints the sha256.
3. Open a pull request here that adds your entry to `registry.json`:
   ```json
   {
     "id": "my-plugin", "name": "My plugin", "author": "you", "description": "…",
     "version": "1.0.0", "api": 1, "min_app": "0.10.0", "uses": { … },
     "url": "https://github.com/you/my-plugin/releases/download/v1.0.0/my-plugin-1.0.0.zip",
     "sha256": "…",
     "source": "https://github.com/you/my-plugin",
     "icon": "https://raw.githubusercontent.com/you/my-plugin/main/icon.svg"
   }
   ```
   The `id`, `name`, `version`, `api`, `min_app` and `uses` must be the same as in your `plugin.json`.
4. A check runs on the pull request (`tools/check.py`). It downloads your zip, compares the sha256 and validates `plugin.json`. Then the code is read by a person before the pull request is merged.

The sha256 pins the exact zip that was reviewed, so Wavelog refuses a download that changed afterwards. For an update, open a new pull request with the new version, url and sha256.

## Trust

A plugin runs inside Wavelog with the same rights as Wavelog: it can read files and use the internet. Python can't be truly sandboxed, so the protection is the review and the pinned sha256. Plugins installed from a file outside this list get a clear warning first.

## Official plugins (for maintainers)

Official plugins live in this repo (`"official": true`). After changing one:

```
python tools/pack.py valorant     # writes dist/valorant-<version>.zip and updates registry.json
python tools/check.py             # rebuilds every official zip and compares
```

Then create a release tagged `<id>-<version>` with the zip from `dist/`, and push `registry.json` after the release is up. The pack is byte-for-byte reproducible, so the check can rebuild official zips from their folders.

## License

MIT
