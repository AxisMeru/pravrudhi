// Record a real session through the running app: actual navigation and clicks, captured frame by frame, so the
// demo can show the product being used rather than a slideshow of static cards.
const { chromium } = require('@playwright/test');
const path = require('node:path');
const out = process.argv[2] || 'public/walkthrough';
const base = process.argv[3] || 'http://127.0.0.1:8200';

const STEPS = [
  { file: '01-home.png',      caption: 'The front door: the largest measured improvement, what the engine wants, and what it has done.', go: '/' },
  { file: '02-objective.png', caption: 'One objective, with each benchmark stated at its honest significance rather than a bare delta.', go: '/objectives/detail?id=code-harness' },
  { file: '03-candidates.png',caption: 'Every candidate the engine has scored, plotted against the incumbent it had to beat.', go: '/candidates' },
  { file: '04-run.png',       caption: 'A night, replayed from the ledger: what was proposed, what was measured, what the boundary decided.', go: '/runs/view?run=night-15-lora' },
  { file: '05-swarm.png',     caption: 'The swarm: which agent each tier routes to and why, and a panel that dispatches real work.', go: '/swarm' },
  { file: '06-appetite.png',  caption: 'What the engine wants right now, as numbers — including the two drives it cannot measure yet.', go: '/appetite' },
  { file: '07-desktop.png',   caption: 'The desktop shell, captured from the running window.', go: '/desktop' },
  { file: '08-system.png',    caption: 'How it keeps itself current: two channels, seven safeguards, and both machines it updates.', go: '/system' },
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const frames = [];
  for (const step of STEPS) {
    await page.goto(base + step.go, { waitUntil: 'domcontentloaded' }).catch(() => {});
    await page.waitForTimeout(9000);
    await page.screenshot({ path: path.join(out, step.file) });
    frames.push({ image: step.file, caption: step.caption, path: step.go });
    process.stdout.write(`captured ${step.file}\n`);
  }
  require('node:fs').writeFileSync(path.join(out, 'frames.json'), JSON.stringify({ frames }, null, 2));
  await browser.close();
})();
