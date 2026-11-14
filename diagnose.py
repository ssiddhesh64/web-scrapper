
import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from camoufox.async_api import AsyncCamoufox


logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s"
)


OUTPUT_DIR = Path("diagnostics")
OUTPUT_DIR.mkdir(exist_ok=True)


async def collect_fingerprint(page):
    """
    Collect browser fingerprint properties available to JavaScript.
    """

    return await page.evaluate("""
    async () => {

        // Browser and navigator information
        const navigatorInfo = {
            userAgent: navigator.userAgent,
            platform: navigator.platform,
            language: navigator.language,
            languages: navigator.languages,
            hardwareConcurrency: navigator.hardwareConcurrency,
            deviceMemory: navigator.deviceMemory ?? null,
            webdriver: navigator.webdriver,
            cookieEnabled: navigator.cookieEnabled,
            maxTouchPoints: navigator.maxTouchPoints,
            vendor: navigator.vendor,
            pdfViewerEnabled: navigator.pdfViewerEnabled
        };

        // Screen information
        const screenInfo = {
            width: screen.width,
            height: screen.height,
            availWidth: screen.availWidth,
            availHeight: screen.availHeight,
            colorDepth: screen.colorDepth,
            pixelDepth: screen.pixelDepth,
            devicePixelRatio: window.devicePixelRatio
        };

        // Window information
        const windowInfo = {
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            outerWidth: window.outerWidth,
            outerHeight: window.outerHeight,
            scrollWidth: document.documentElement.scrollWidth,
            scrollHeight: document.documentElement.scrollHeight
        };

        // Timezone information
        const timezoneInfo = {
            timezone: Intl.DateTimeFormat()
                .resolvedOptions().timeZone,
            locale: Intl.DateTimeFormat()
                .resolvedOptions().locale,
            offsetMinutes: new Date().getTimezoneOffset()
        };

        // WebGL information
        function getWebGLInfo() {

            const canvas = document.createElement("canvas");

            const gl = canvas.getContext("webgl");

            const gl2 = canvas.getContext("webgl2");

            if (!gl) {
                return {
                    available: false
                };
            }

            const ext = gl.getExtension(
                "WEBGL_debug_renderer_info"
            );

            let debugInfo = {};

            if (ext) {
                debugInfo = {
                    vendor: gl.getParameter(
                        ext.UNMASKED_VENDOR_WEBGL
                    ),
                    renderer: gl.getParameter(
                        ext.UNMASKED_RENDERER_WEBGL
                    )
                };
            }

            return {
                available: true,
                webgl2Available: !!gl2,
                vendor: gl.getParameter(gl.VENDOR),
                renderer: gl.getParameter(gl.RENDERER),
                ...debugInfo
            };
        }

        // Canvas rendering information
        function getCanvasInfo() {

            const canvas = document.createElement("canvas");

            canvas.width = 300;
            canvas.height = 100;

            const ctx = canvas.getContext("2d");

            if (!ctx) {
                return {
                    available: false
                };
            }

            ctx.font = "18px Arial";
            ctx.fillStyle = "rgb(120, 50, 200)";
            ctx.fillRect(10, 10, 100, 50);

            ctx.fillStyle = "#ff0000";
            ctx.fillText("Camoufox diagnostic", 15, 45);

            return {
                available: true,
                dataURLLength: canvas.toDataURL().length
            };
        }

        // Browser feature availability
        const features = {
            localStorage: (() => {
                try {
                    localStorage.setItem("__test", "1");
                    localStorage.removeItem("__test");
                    return true;
                } catch {
                    return false;
                }
            })(),

            sessionStorage: (() => {
                try {
                    sessionStorage.setItem("__test", "1");
                    sessionStorage.removeItem("__test");
                    return true;
                } catch {
                    return false;
                }
            })(),

            webAssembly: typeof WebAssembly !== "undefined",
            webRTC: typeof RTCPeerConnection !== "undefined",
            serviceWorker: "serviceWorker" in navigator,
            permissions: "permissions" in navigator,
            bluetooth: "bluetooth" in navigator
        };

        // Font availability
        const fonts = [
            "Arial",
            "Helvetica",
            "Times New Roman",
            "Courier New",
            "Georgia",
            "Verdana",
            "Menlo",
            "Monaco"
        ];

        const fontInfo = {};

        for (const font of fonts) {
            fontInfo[font] = document.fonts.check(
                "12px " + font
            );
        }

        return {
            timestamp: new Date().toISOString(),
            navigator: navigatorInfo,
            screen: screenInfo,
            window: windowInfo,
            timezone: timezoneInfo,
            webgl: getWebGLInfo(),
            canvas: getCanvasInfo(),
            features: features,
            fonts: fontInfo
        };
    }
    """)


async def main(headless=False):

    print("=" * 60)
    print("CAMOUFOX FINGERPRINT DIAGNOSTICS")
    print("=" * 60)

    requests = []
    responses = []

    async with AsyncCamoufox(
        headless=headless,
        os="macos",
        debug=True
    ) as browser:

        print("\nBrowser version:", browser.version)

        page = await browser.new_page()

        # Capture navigation request headers.
        page.on(
            "request",
            lambda request: requests.append({
                "url": request.url,
                "method": request.method,
                "headers": request.headers
            }) if request.is_navigation_request() else None
        )

        # Capture document responses.
        page.on(
            "response",
            lambda response: responses.append({
                "url": response.url,
                "status": response.status
            }) if response.request.resource_type == "document" else None
        )

        # url = "https://www.browserscan.net/"
        url = "https://pixelscan.net/fingerprint-check"

        print("\nOpening:", url)

        try:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            print(
                "Initial HTTP status:",
                response.status if response else None
            )

            # Allow fingerprint checks to complete.
            await asyncio.sleep(30)

            print("\nCollecting fingerprint...")

            fingerprint = await collect_fingerprint(page)

            # Collect visible page content.
            page_info = {
                "title": await page.title(),
                "url": page.url,
                "text": (await page.locator("body").inner_text())[:15000]
            }

            # Save screenshot.
            await page.screenshot(
                path=str(OUTPUT_DIR / "browserscan.png"),
                full_page=True
            )

            # Build complete diagnostic report.
            report = {
                "browser_version": browser.version,
                "headless": headless,
                "fingerprint": fingerprint,
                "page": page_info,
                "navigation_requests": requests,
                "document_responses": responses
            }

            # Save JSON.
            output_file = OUTPUT_DIR / "report.json"

            output_file.write_text(
                json.dumps(report, indent=4, default=str),
                encoding="utf-8"
            )

            # Save HTML.
            html = await page.content()

            (OUTPUT_DIR / "page.html").write_text(
                html,
                encoding="utf-8"
            )

            # Print results.
            print("\n" + "=" * 60)
            print("FINGERPRINT RESULTS")
            print("=" * 60)

            print(json.dumps(
                fingerprint,
                indent=4,
                default=str
            ))

            print("\nPage title:", page_info["title"])
            print("Final URL:", page_info["url"])

            print("\nSaved diagnostic files:")
            print(output_file)
            print(OUTPUT_DIR / "page.html")
            print(OUTPUT_DIR / "browserscan.png")
            
            extra = await page.evaluate("""
            async () => {

                const audioContext = new (
                    window.OfflineAudioContext ||
                    window.webkitOfflineAudioContext
                )(1, 44100, 44100);

                const oscillator = audioContext.createOscillator();
                const compressor = audioContext.createDynamicsCompressor();

                oscillator.connect(compressor);
                compressor.connect(audioContext.destination);

                oscillator.start(0);

                const audioBuffer = await audioContext.startRendering();

                const samples = audioBuffer.getChannelData(0);

                return {
                    buildID: navigator.buildID ?? null,

                    oscpu: navigator.oscpu ?? null,

                    userAgentData:
                        navigator.userAgentData
                            ? await navigator.userAgentData.getHighEntropyValues([
                                "platform",
                                "platformVersion",
                                "architecture",
                                "bitness",
                                "model"
                            ])
                            : null,

                    screenOrientation: screen.orientation?.type,

                    touchPoints: navigator.maxTouchPoints,

                    timezone: Intl.DateTimeFormat()
                        .resolvedOptions().timeZone,

                    audio: {
                        sampleRate: audioBuffer.sampleRate,
                        length: samples.length,
                        firstSamples: Array.from(samples.slice(0, 20))
                    }
                };
            }
            """)

            print(extra)

        except Exception as error:
            logging.exception("Diagnostic failed: %s", error)


if __name__ == "__main__":
    asyncio.run(main(headless=False))
