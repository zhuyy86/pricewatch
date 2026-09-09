const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { pathToFileURL } = require('url');
const fs = require('fs');
const path = require('path');

(async () => {
  const report = path.resolve('outputs/demo-report.html');
  if (!fs.existsSync(report)) throw new Error('Run scripts/demo_history.py first.');
  fs.mkdirSync('docs', { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page.goto(pathToFileURL(report).href, { waitUntil: 'load' });
    await page.screenshot({ path: 'docs/demo-report.png', fullPage: true });
    if ((await page.locator('tbody tr').count()) !== 2) {
      throw new Error('Expected exactly two demo change rows.');
    }
    console.log('PASS: report opened and two change rows were rendered.');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
