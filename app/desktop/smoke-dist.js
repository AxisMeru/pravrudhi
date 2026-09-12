'use strict';
const {spawn, spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {createSmokeReporter} = require('./lib/smoke');
const {terminateGroup} = require('./lib/lifecycle');
// Which edition to smoke: `npm run smoke:dist -- studio`, defaulting to the product. The builds write to
// dist/<edition>/ so both can exist at once, which is the point of having two of them.
const wanted = (process.argv[2] || 'product').trim().toLowerCase();
const distDir = path.join(__dirname, 'dist', wanted);
const appImageName = fs.existsSync(distDir) ? fs.readdirSync(distDir).find(f => f.endsWith('.AppImage')) : null;
// A distinct, writable directory: the packaged main process cannot write its
// report next to itself, since that would be inside the read-only app.asar.
const smokeDir = path.join(__dirname, '.smoke-dist');
const reportFile = path.join(smokeDir, '.smoke/report.json');
const failure = async error => {
  console.error(`Packaged desktop smoke failed: ${error.message}`);
  await createSmokeReporter(reportFile).fail(error);
  process.exitCode = 1;
};
async function main() {
  if (!appImageName) throw new Error(`No .AppImage found in dist/${wanted}/. Run \`npm run dist:linux\` (or dist:studio:linux) first.`);
  const appImagePath = path.join(distDir, appImageName);
  fs.chmodSync(appImagePath, 0o755);
  fs.rmSync(smokeDir, {recursive: true, force: true});
  fs.mkdirSync(smokeDir, {recursive: true});
  const env = {...process.env, ELECTRON_ENABLE_LOGGING: '1', PRAVRUDHI_DESKTOP_SMOKE: '1', PRAVRUDHI_DESKTOP_SMOKE_DIR: smokeDir};
  const hasXvfb = !spawnSync('xvfb-run', ['--help'], {stdio: 'ignore'}).error;
  // This environment cannot configure the root-owned SUID sandbox helper, and the
  // AppImage's own FUSE mount is unavailable in a sandbox, so extract and run instead.
  // BrowserWindow's sandboxed, isolated renderer (webPreferences.sandbox) stays enabled.
  const args = ['--appimage-extract-and-run', '--no-sandbox', '--disable-dev-shm-usage'];
  if (!hasXvfb) {
    env.ELECTRON_DISABLE_GPU = '1';
    args.push('--headless', '--ozone-platform=headless', '--disable-gpu');
  }
  const child = spawn(hasXvfb ? 'xvfb-run' : appImagePath, hasXvfb ? ['-a', appImagePath, ...args] : args, {env, cwd: smokeDir, stdio: 'inherit', detached: true});
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; terminateGroup(child, {grace: 5000}).catch(error => console.error(error)); }, 120000);
  const forward = () => terminateGroup(child, {grace: 5000}).catch(error => console.error(error));
  process.once('SIGINT', forward); process.once('SIGTERM', forward);
  await new Promise(resolve => {
    child.once('error', async error => { clearTimeout(timer); await failure(error); resolve(); });
    child.once('close', async (code, signal) => {
      clearTimeout(timer); process.removeListener('SIGINT', forward); process.removeListener('SIGTERM', forward);
      try {
        const report = JSON.parse(fs.readFileSync(reportFile, 'utf8'));
        if (timedOut || code !== 0 || report.launched !== true || report.engine_found !== true || report.health_ok !== true || !report.engine_url || !report.page_title || !Array.isArray(report.errors) || report.errors.length) {
          throw new Error(timedOut ? 'Launch timed out.' : report.errors?.join('; ') || `Packaged app exited ${signal || code} without a successful report.`);
        }
        // The edition is what makes these two builds different installs rather than two copies of one. It has
        // been got wrong twice by build configuration that looked correct, so the packaged app is made to say
        // which it is and this refuses a build that came back as the other one.
        if (report.edition !== wanted) {
          throw new Error(`Built the ${wanted} edition but the packaged app ran as ${report.edition ?? 'nothing'}.`);
        }
        // Studio has no account surface at all (main.js and preload.js carry zero auth/product references),
        // so its packaged smoke must see 'not-applicable' rather than a stray or newly-introduced sign-in
        // state — the standing check that Studio stays the operator's edition with nothing of the product's
        // sign-in in it (see AxisMeru/pravrudhi-app's desktop/smoke-dist.js for the product's own check).
        if (wanted === 'studio' && report.signin_state !== 'not-applicable') {
          throw new Error(`Studio build's signin_state was '${report.signin_state}', not 'not-applicable' — Studio should have no sign-in surface at all.`);
        }
        console.log(`Packaged ${wanted} app loaded ${report.engine_url}: ${report.page_title}`);
        process.exitCode = 0;
      } catch (error) {
        // Preserve main-process observations on failure; only fabricate a report if
        // the packaged app could not initialize far enough to write its own.
        if (fs.existsSync(reportFile)) { console.error(`Packaged desktop smoke failed: ${error.message}`); process.exitCode = 1; }
        else await failure(new Error(timedOut ? 'Launch timed out before reporting.' : `Packaged app exited ${signal || code}: ${error.message}`));
      }
      resolve();
    });
  });
}
main().catch(failure);
