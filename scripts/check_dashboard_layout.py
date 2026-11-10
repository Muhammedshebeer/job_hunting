"""Check already-rendered synthetic preview pages, using an isolated Chrome session."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'output' / 'dashboard_preview'
BROWSER = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
if not BROWSER.exists() or not (OUTPUT / 'index.html').exists():
    raise SystemExit('Render synthetic previews first and install Chrome, then serve output/dashboard_preview on 127.0.0.1:8765')

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=str(BROWSER), headless=True)
    for label, width, height, filename in (
        ('desktop', 1440, 1200, 'index.html'),
        ('mobile', 390, 1200, 'index.html'),
        ('tablet', 768, 1200, 'index.html'),
        ('draft', 1440, 1200, 'draft.html'),
        ('draft-mobile', 390, 1200, 'draft.html'),
        ('history', 1440, 1200, 'history.html'),
        ('history-mobile', 390, 1200, 'history.html'),
    ):
        page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
        page.goto('http://127.0.0.1:8765/' + filename, wait_until='networkidle')
        dimensions = page.evaluate('({viewport: innerWidth, content: document.documentElement.scrollWidth})')
        page.screenshot(path=str(OUTPUT / (label + '-verified.png')), full_page=True)
        print(f"{label}: viewport={dimensions['viewport']}, content={dimensions['content']}")
        if dimensions['content'] > dimensions['viewport']:
            raise SystemExit('Horizontal overflow in synthetic preview: ' + label)
        page.close()
    browser.close()
