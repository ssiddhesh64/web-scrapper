# Camoufox Quick Reference

Checked against the official docs (camoufox.com/python/*) and the installed source in
`.venv` on 2026-11-14. Versions: **camoufox 0.5.9**, browserforge 1.2.4, playwright 1.62.0.

Camoufox is a patched Firefox driven through **Playwright**. Once you have a `page`, everything
is normal Playwright API (`page.goto`, `page.locator`, `page.wait_for_selector`, ...). Camoufox
only changes how the browser is **launched** and **fingerprinted**.

---

## 1. Install / CLI

```bash
uv add "camoufox[geoip]"        # geoip extra: strongly recommended when using proxies
uv run camoufox fetch           # download the browser binary (needed once)
uv run camoufox version         # versions, active browser, paths (use in bug reports)
uv run camoufox path            # where the browser is installed
uv run camoufox list            # installed browser versions  (list all = all synced)
uv run camoufox sync            # refresh the list of available versions
uv run camoufox set official/stable   # follow a channel / pin a version
uv run camoufox test https://example.com   # opens Playwright inspector (window looks broken, that's expected)
uv run camoufox remove          # delete all camoufox data (-y to skip prompt)
uv run camoufox server          # run as a Playwright websocket server (experimental)
```

---

## 2. Launching

### Async (what we use)
```python
from camoufox.async_api import AsyncCamoufox

async with AsyncCamoufox(headless=False, os="macos", humanize=True) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com", wait_until="domcontentloaded")
```

### Sync
```python
from camoufox.sync_api import Camoufox

with Camoufox() as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

### Persistent profile (keeps cookies/localStorage between runs, so it looks like a returning user)
```python
async with AsyncCamoufox(persistent_context=True, user_data_dir="profiles/reddit_1") as context:
    page = await context.new_page()     # NOTE: you get a BrowserContext, not a Browser
```
Persistent contexts can **not** be served with `launch_server`.

### Many identities from one browser (each context gets its own fingerprint)
```python
from camoufox.async_api import AsyncCamoufox, AsyncNewContext

async with AsyncCamoufox() as browser:
    ctx = await AsyncNewContext(browser, os="macos", proxy={"server": "http://host:port"})
    page = await ctx.new_page()
```
`AsyncNewContext(browser, *, preset=None, os=None, ff_version=None, webrtc_ip=None,
proxy=None, geolocation=None, **playwright_new_context_kwargs)`.
If you give it a `proxy`, the WebRTC IP and timezone are looked up from the proxy's exit IP.
Caveat: unless the browser was launched with `canvas_noise=True`, contexts claim the **host's**
OS and GPU, and asking for a different `os` raises `ValueError`.

### Build options once, reuse them
```python
from camoufox import launch_options
opts = launch_options(os="macos", humanize=True)   # dict you can inspect/log
async with AsyncCamoufox(from_options=opts) as browser: ...
```

---

## 3. Launch parameters

Any Playwright Firefox launch option is also accepted (e.g. `proxy`, `args`, `env`, `executable_path`).

| Param | Type | What it does / advice |
|---|---|---|
| `os` | `"windows" \| "macos" \| "linux"` or list | OS to fingerprint as. Default: random. Matching your real OS is the most consistent choice. |
| `headless` | `bool \| "virtual" \| "virtual-gpu"` | Default `False`. `"virtual"` (Linux only) runs a headed browser on Xvfb, which is the recommended "headless". Plain `True` may become detectable. |
| `humanize` | `bool \| float` | Human-like cursor movement. `True` (about 1.5s max) or a max duration in seconds, e.g. `2.0`. **Turn it on.** |
| `screen` | `camoufox.fingerprints.Screen` | Constrains generated screen size, e.g. `Screen(max_width=1920, max_height=1080)`. **In 0.5.x import it from `camoufox.fingerprints`.** The docs still show `browserforge.fingerprints.Screen`, which crashes with `AttributeError: 'Screen' object has no attribute 'as_conditions'`. |
| `window` | `(w, h)` | Fixed window size. **Debugging only**, because a fixed size is fingerprintable. |
| `geoip` | `bool \| str` | `True` = derive timezone, locale, lat/long and WebRTC IP from your (proxy's) exit IP. Use **with a proxy**. |
| `geoip_db` | `str` | Which GeoIP DB, e.g. `"MaxMind GeoLite2"`. |
| `locale` | `str \| list` | `"en-US"`, `"US"`, `["en-US","fr-FR"]`. The first entry drives `Intl`. Usually let `geoip` set this. |
| `proxy` | `dict` | `{"server": "http://host:port", "username": "...", "password": "..."}`. Residential works best. |
| `persistent_context` + `user_data_dir` | `bool`, `str` | Reuse a profile directory between runs. Returns a `BrowserContext`. |
| `block_images` | `bool` | Skip image downloads. Saves proxy bandwidth. |
| `block_webrtc` | `bool` | Disable WebRTC entirely (prevents IP leaks, but "no WebRTC" is itself a little unusual). |
| `block_webgl` | `bool` | Disable WebGL. **Special cases only**, because it causes leaks. |
| `webgl_config` | `(vendor, renderer)` | Must be a valid pair for the chosen `os`, or it leaks. Example: `("Apple", "Apple M1, or similar")`. |
| `disable_coop` | `bool` | Lets you click inside cross-origin iframes (e.g. the Cloudflare Turnstile checkbox). |
| `fonts` / `custom_fonts_only` | `list[str]`, `bool` | Extra system fonts to expose. |
| `addons` | `list[str]` | Paths to **extracted** addon folders (`.xpi` → rename `.zip` → unzip). |
| `exclude_addons` | `list[DefaultAddons]` | e.g. `[DefaultAddons.UBO]` to drop the bundled uBlock Origin. |
| `fingerprint_preset` | `bool \| dict` | `True` = a random real-world fingerprint preset instead of a generated one. |
| `fingerprint` | `dict` | Supply a browserforge fingerprint yourself. |
| `canvas_noise` | `bool` | Canvas noise. Also needed if `AsyncNewContext` should claim a non-host OS. |
| `enable_cache` | `bool` | Default off (saves memory). **Required for `page.go_back()` / `go_forward()`.** |
| `main_world_eval` | `bool` | Allows `page.evaluate("mw:...")` (see section 5). |
| `firefox_user_prefs` | `dict` | Raw Firefox prefs. |
| `config` | `dict` | Raw Camoufox config properties (e.g. `"webrtc:ipv4"`). **Not recommended**: the library fills these for you and warns on conflicts. |
| `ff_version` | `int` | Firefox version to claim. |
| `browser` | `str` | Use a specific installed browser build, e.g. `"official/beta.20"`. |
| `pin_cpu_cores` | `bool` | Pin the browser to CPU cores. |
| `debug` | `bool` | Print the config being used. |
| `i_know_what_im_doing` | `bool` | Silence Camoufox's leak warnings. **Don't**: the warnings are useful. |

---

## 4. Everyday Playwright API (after launch)

```python
# Navigation
resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)  # "load" | "networkidle" | "commit"
resp.status, resp.headers, resp.url
await page.wait_for_load_state("networkidle")
await page.reload(); await page.go_back()           # go_back needs enable_cache=True

# Waiting (prefer these to asyncio.sleep)
await page.wait_for_selector("shreddit-post", timeout=15_000)
await page.locator("article").first.wait_for()
await page.wait_for_url("**/comments/**")
await page.wait_for_timeout(1500)                   # explicit pause

# Reading content
await page.title()
await page.content()                                # full HTML
await page.locator("body").inner_text()
await page.locator("a.title").all_inner_texts()
await page.locator("a").evaluate_all("els => els.map(e => e.href)")
await page.get_attribute("shreddit-post", "permalink")
for el in await page.locator("shreddit-post").all(): ...

# Interaction (with humanize=True, the mouse moves like a human)
await page.click("text=Accept")
await page.locator("input[name=q]").fill("python")
await page.keyboard.type("hello", delay=120)        # per-key delay in ms
await page.mouse.wheel(0, 800)                      # scroll
await page.mouse.move(400, 300)
await page.hover("nav")

# Network
await page.route("**/*.{png,jpg,gif,webp,mp4}", lambda r: r.abort())   # block resources
page.on("response", handler)                        # sniff XHR/JSON responses
async with page.expect_response(lambda r: "/svc/shreddit" in r.url) as info:
    await page.mouse.wheel(0, 2000)
data = await (await info.value).json()

# Session state
await context.cookies(); await context.add_cookies([...])
await context.storage_state(path="state.json")      # save cookies + localStorage
# reload state: browser.new_context(storage_state="state.json")

# Debug artifacts
await page.screenshot(path="shot.png", full_page=True)
page.on("console", lambda m: print(m.text))
```

---

## 5. Running JavaScript

- By default `page.evaluate()` runs in an **isolated world**. It can read the DOM, can't modify
  page JS state, and is invisible to the page:
  `await page.evaluate("document.querySelector('h1').innerText")`
- Main world (the page can see it): launch with `main_world_eval=True`, then prefix with `mw:`:
  `await page.evaluate("mw:document.querySelector('h1').remove()")`.
  Only JSON-serialisable return values, no element handles.
  **Anything run in the main world is detectable. Use it only if you absolutely must.**

---

## 6. GeoIP + proxy

```python
async with AsyncCamoufox(
    geoip=True,
    proxy={"server": "http://gate.example.com:8080", "username": "u", "password": "p"},
) as browser: ...
```
Timezone, locale, geolocation and WebRTC IP then all match the proxy's exit IP. A mismatch
(for example a US IP with an Asia/Kolkata timezone) is a classic bot signal.

## 7. Remote server (experimental)

```python
from camoufox.server import launch_server
launch_server(headless=True, geoip=True, port=1234, ws_path="hello")   # ws://localhost:1234/hello
# client (any Playwright language):
browser = p.firefox.connect("ws://localhost:1234/hello")
```
A single browser instance means fingerprints don't rotate. Restart the server to rotate.

## 8. Do / Don't (from the docs)

- ✅ Let Camoufox generate the fingerprint. ❌ Don't set the user agent, `navigator.*` or
  `config` values by hand: an inconsistent fingerprint is worse than a default one.
- ❌ No fixed `window` outside debugging. ❌ `block_webgl` only for special cases.
- ✅ `webgl_config` must match `os`.
- ✅ Residential proxies + `geoip=True`.
- ✅ Prefer headed mode, or `headless="virtual"` on Linux, over `headless=True`.
- ❌ Avoid `mw:` main-world JS.
- ✅ Run `diagnose.py` / the `/fingerprint-check` skill after config changes.
