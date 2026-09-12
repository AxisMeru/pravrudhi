import { chromium } from 'playwright';

const base = process.argv[2];
const outdir = process.argv[3];
const paths = process.argv.slice(4);

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1400 } });

for (const p of paths) {
  const page = await ctx.newPage();
  const errors = [];
  const failed = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)); });
  page.on('pageerror', e => errors.push('PAGEERROR: ' + String(e).slice(0, 300)));
  page.on('requestfailed', r => failed.push(r.url() + ' :: ' + (r.failure()?.errorText || '')));
  page.on('response', r => { if (r.status() >= 400) failed.push(r.status() + ' ' + r.url()); });
  let status = 'n/a';
  try {
    const resp = await page.goto(base + p, { waitUntil: 'networkidle', timeout: 30000 });
    status = resp ? resp.status() : 'null';
  } catch (e) {
    errors.push('NAV: ' + String(e).slice(0, 200));
  }
  await page.waitForTimeout(4000);
  let text = '';
  try { text = (await page.locator('body').innerText()).replace(/\n{2,}/g, '\n'); } catch { text = 'NO BODY'; }
  const name = (p.replace(/[^a-z0-9]/gi, '_') || 'root');
  await page.screenshot({ path: `${outdir}/${name}.png`, fullPage: true }).catch(() => {});
  console.log('==== ' + p + ' HTTP ' + status + ' ====');
  console.log('--- console errors: ' + (errors.length ? JSON.stringify(errors) : 'none'));
  console.log('--- failed/4xx requests: ' + (failed.length ? JSON.stringify(failed) : 'none'));
  console.log('--- rendered text (first 2500) ---');
  console.log(text.slice(0, 2500));
  console.log('');
  await page.close();
}
await browser.close();
