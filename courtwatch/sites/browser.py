"""
Headless Chromium (Playwright) for the sites that only work inside a real browser session:
Mesa eCourt (ASP.NET session + reCAPTCHA v3 token set by the page's own script) and the Power BI report.
The browser simply loads the pages the way a person's browser would; nothing is spoofed or bypassed.
"""
import contextlib, os
from playwright.sync_api import sync_playwright
from .base import UA

@contextlib.contextmanager
def page(timezone="America/Phoenix"):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        ctx = b.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="en-US",
                            timezone_id=os.environ.get("TZ", timezone))
        pg = ctx.new_page()
        pg.set_default_timeout(60000)
        try:
            yield pg
        finally:
            with contextlib.suppress(Exception):
                b.close()
