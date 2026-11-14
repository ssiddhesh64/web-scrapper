---
name: fingerprint-check
description: Run the Camoufox fingerprint diagnostic (diagnose.py) and analyse diagnostics/report.json for bot-detection leaks. Use after changing Camoufox/browserforge config, or when a site starts blocking the scraper.
---

1. If `diagnostics/report.json` exists, read it first and keep it as the "before" baseline.
2. Run `uv run python diagnose.py`. It opens a visible browser on pixelscan.net and waits about 30s, so use a timeout of at least 120s. It overwrites `diagnostics/report.json`, `page.html` and `browserscan.png`.
3. Read the new `diagnostics/report.json` and check:
   - `navigator.webdriver` must be `false`.
   - `navigator.userAgent`, `platform` and `oscpu` must describe the same OS (e.g. all macOS) and agree with the `navigation_requests` headers (User-Agent, Accept-Language).
   - `timezone` and `locale` should be plausible for the exit IP (run `uv run python check_ip.py` if that's unclear).
   - WebGL `vendor`/`renderer` and `screen`/`window` sizes must be consistent with the claimed OS and device (e.g. `innerHeight` < `outerHeight` <= `availHeight`).
   - `page.text` shows pixelscan's own verdict. Quote any "inconsistent", "bot" or "masking detected" lines.
   - `document_responses` should contain no 403 or 429 responses.
4. Look at `diagnostics/browserscan.png` to confirm the visual verdict.
5. Report: a pass/fail list per check, what changed compared with the baseline, and the specific Camoufox options (`os`, `screen`, `locale`, `geoip`, `humanize`, etc.) that would fix each failure.
