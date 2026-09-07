'use strict';
const {app, BrowserWindow, Menu, Tray, nativeImage, ipcMain, dialog, shell, screen} = require('electron');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {discoverEngine, pollHealth, parseDoctor, linkPolicy, readState, writeState, validBounds} = require('./lib/core');
const {recovery} = require('./lib/recovery');
const {createApiClient} = require('./lib/api');
const {selectConnection, defaultWorkspace} = require('./lib/connection');
const {createProcessOwner, singleInstance, focusWindow} = require('./lib/lifecycle');
const {engineEnv, readEdition, userDataName} = require('./lib/edition');
const edition = readEdition(process.resourcesPath);
const {engineMenu, trayState} = require('./lib/menu');
const {createSmokeReporter} = require('./lib/smoke');
const {shellIsStale} = require('./lib/updates');
// The newest release this shell has heard of, refreshed whenever the engine is asked. The engine
// updates itself; this bundle is replaced by downloading a new one, so a shell can sit behind a
// current engine and nothing said so until now.
let latestTag = null;
const smokeMode = process.env.PRAVRUDHI_DESKTOP_SMOKE === '1';
// A packaged app's __dirname resolves inside the read-only app.asar, so a packaged
// smoke run redirects its report and userData to a writable directory outside it.
const smokeDir = process.env.PRAVRUDHI_DESKTOP_SMOKE_DIR || __dirname;
const offscreen = process.env.ELECTRON_DISABLE_GPU === '1';
if (offscreen) app.disableHardwareAcceleration();
// Keep the two editions' settings apart; see lib/edition.js::userDataName. A smoke run overrides this
// again below, because a packaged smoke must write outside the read-only app.asar.
app.setPath('userData', path.join(app.getPath('appData'), userDataName(edition)));
if (smokeMode) app.setPath('userData', path.join(smokeDir, '.smoke/user-data'));
const smoke = smokeMode ? createSmokeReporter(path.join(smokeDir, '.smoke/report.json'), {edition}) : null;
let smokeExitCode = 1, smokeFinished = false;
async function finishSmoke(error) {
  if (!smoke || smokeFinished) return;
  smokeFinished = true;
  try {
    if (!error && process.env.PRAVRUDHI_DESKTOP_SHOT) {
      const contents = [...windows][0].webContents;
      const observed = await contents.executeJavaScript(`new Promise((resolve, reject) => {
        const deadline = Date.now() + 30000;
        function check() {
          const text = document.body?.innerText.trim() || '';
          if (text.length > 40) return resolve({url: location.href, title: document.title, body: text.slice(0, 2000)});
          if (Date.now() >= deadline) return reject(new Error('Engine interface remained blank.'));
          setTimeout(check, 200);
        }
        check();
      })`);
      // Await capture before quitting; the initial diagnostics page is not evidence
      // that the engine frontend rendered successfully.
      const fs = require('node:fs');
      fs.writeFileSync(process.env.PRAVRUDHI_DESKTOP_SHOT, (await contents.capturePage()).toPNG());
      fs.writeFileSync(`${process.env.PRAVRUDHI_DESKTOP_SHOT}.json`, JSON.stringify(observed, null, 2));
    }
    smokeExitCode = error ? await smoke.fail(error) : await smoke.finish({getTitle:()=>[...windows][0].webContents.getTitle(),health:api.health});
  } catch (failure) { console.error('Smoke report:', failure); smokeExitCode = await smoke.fail(failure); }
  app.quit();
}
const docs = 'https://github.com/SharathSPhD/pravrudhi#readme';
const statusFile = path.join(__dirname, 'renderer/index.html');
const statusURL = pathToFileURL(statusFile).href;
let settings, stateFile, workspace, tray, engine, controller, quitting = false, queue = Promise.resolve();
const windows = new Set(), processes = createProcessOwner();
let status = {phase: 'starting', detail: 'Finding your installed engine…', checks: [], version: 'Unknown', origin: null};
const api = createApiClient(()=>status.origin);
const engineController = {restart:()=>serialize(start),stop:()=>serialize(stop),checkForUpdates:updates,openWorkspace:async()=>{ const error = await shell.openPath(workspace); if (error) throw new Error(error); }};
function persist() { writeState(stateFile, settings); }
function publish(patch) { status = {...status, ...patch}; refreshTray(); }
function statusScreens() { for (const w of windows) w.loadFile(statusFile).catch(() => {}); }
function focus() { focusWindow(windows, createWindow); }
function safe(action) { return () => Promise.resolve().then(action).catch(e => dialog.showErrorBox('Pravrudhi', e.message)); }
function serialize(action) { controller?.abort(); const next = queue.then(action); queue = next.catch(() => {}); return next; }
function launch(args) {
  if (quitting) throw new Error('Application is shutting down.');
  // The engine names itself from PRAVRUDHI_EDITION, so this build's stamp travels with every process it starts.
  return processes.launch(status.binary,args,{cwd:workspace,env:engineEnv(process.env,edition)});
}
const terminate = child => processes.stop(child);
async function command(args, timeout = 30000) {
  if (!status.binary) throw new Error('Locate an installed engine first.');
  const child = launch(args);
  return new Promise((resolve, reject) => {
    let out = '', err = '', finished = false;
    const finish = async (error) => { if (finished) return; finished = true; clearTimeout(timer); try { await terminate(child); error ? reject(error) : resolve(out); } catch (failure) { reject(failure); } };
    const timer = setTimeout(() => finish(new Error('Engine command timed out.')), timeout);
    child.stdout.on('data', b => { out += b; if (out.length > 4 * 1024 * 1024) finish(new Error('Engine output exceeded the limit.')); });
    child.stderr.on('data', b => { err = (err + b).slice(-12000); });
    child.once('error', e => finish(e));
    child.once('close', code => finish(code && !out.trim() ? new Error(err || `Engine exited with code ${code}`) : null));
  });
}
async function doctor() {
  publish({doctorBusy: true});
  try { const checks = parseDoctor(await command(['doctor', '--json', '--root', workspace])).map(check => ({...check, recovery:recovery(check,status.binary,workspace)})); publish({checks, doctorError: null}); return checks; }
  catch (e) { publish({doctorError: e.message}); return []; }
  finally { publish({doctorBusy: false}); }
}
async function stop() {
  controller?.abort();
  const old = engine, attached = status.attached; engine = null;
  if (old) await terminate(old);
  publish({phase:'stopped', origin:null, attached:false, detail:attached ? 'Disconnected. The externally managed engine is still running.' : 'Engine stopped. Your workspace is ready when you are.'}); statusScreens();
}
async function start() {
  await stop();
  if (quitting) return;
  publish({phase:'starting', detail:'Finding your installed engine…', checks:[], doctorError:null});
  controller = new AbortController();
  const currentController = controller;
  let processFailure = '';
  try {
    const connection = await selectConnection({
      candidates:[process.env.PRAVRUDHI_ENGINE_URL, settings.engineURL, 'http://127.0.0.1:8008'],
      discover:()=>discoverEngine({saved:settings.enginePath})
    });
    const {binary, origin, attached} = connection;
    currentController.signal.throwIfAborted();
    publish({binary, attached, version:'Unknown', detail:attached ? 'Connected to your running engine.' : `Starting ${binary} · waiting for /api/health…`});
    smoke?.engine(origin);
    if (!attached) {
      const port = new URL(origin).port;
      const child = launch(['app', '--no-browser', '--port', port, '--root', workspace]); engine = child;
      let stderr = '';
      child.stderr.on('data', b => { stderr = (stderr + b).slice(-6000); }); child.stdout.resume();
      child.on('error', e => { processFailure = e.message; currentController.abort(); });
      child.on('exit', (code, signal) => {
        if (engine !== child) return;
        processFailure = stderr || `Engine exited (${signal || code}).`; currentController.abort();
        if (status.phase === 'running') {
          engine = null; publish({phase:'error', origin:null, detail:processFailure}); statusScreens();
          safe(async () => { await terminate(child); if (!quitting) await doctor(); })();
        }
      });
    }
    try { await pollHealth(`${origin}/api/health`, {signal:currentController.signal}); } catch(e) { throw new Error(processFailure || e.message); }
    const response = await fetch(origin, {signal: AbortSignal.timeout(5000)});
    if (!response.ok || !(response.headers.get('content-type') || '').includes('text/html')) throw new Error('The engine is healthy, but its frontend is not installed. See the installation instructions to prepare the interface.');
    currentController.signal.throwIfAborted();
    publish({phase:'running', origin, detail:attached ? 'Connected to an existing engine. Stop disconnects this desktop; the external engine stays running.' : 'Engine running'});
    // Learn the newest release once the engine can answer, so the status line knows whether this shell is
    // behind without anyone opening the update dialog. Without this `latestTag` stayed null until someone
    // chose "Check for updates" by hand, and a stale shell looked identical to a current one — which is how
    // both of the operator's machines ran a superseded build while their engines stayed up to date.
    // Quiet by construction: the engine caches its own check, and a failure here must never disturb a start.
    if (!smokeMode) api.update().then(r => { latestTag = r?.latest?.tag ?? latestTag; }).catch(() => {});
    settings.engineURL = origin; persist();
    const health = await api.health(); publish({version:health.version || 'Unknown'});
    await doctor();
    if (smoke) {
      // Exercise the actual sandboxed first-run renderer and its invoke calls.
      const window = [...windows][0];
      await window.webContents.executeJavaScript(`new Promise((resolve, reject) => {
        const deadline = Date.now() + 45000;
        function check() {
          if (document.body.dataset.apiReady === 'true' && document.body.dataset.doctorReady === 'true') {
            if (document.body.dataset.apiError) return reject(new Error(document.body.dataset.apiError));
            return resolve();
          }
          if (Date.now() >= deadline) return reject(new Error('First-run API screen did not finish loading.'));
          setTimeout(check, 100);
        }
        check();
      })`);
      await window.loadURL(origin);
      await finishSmoke();
    }
  } catch (e) {
    const old = engine; engine = null; if (old) await terminate(old);
    if (quitting || (currentController.signal.aborted && !processFailure)) return;
    publish({phase:'error', origin:null, detail:e.message}); statusScreens(); if (smoke) await finishSmoke(e); else await doctor();
  }
}
async function locate() {
  const result = await dialog.showOpenDialog({title:'Locate the Pravrudhi engine executable', properties:['openFile']});
  if (!result.canceled) { settings.enginePath = result.filePaths[0]; persist(); await serialize(start); }
  return status;
}
let updating = false;
async function updates() {
  if (updating) return;
  updating = true;
  try {
    const result = await api.update();
    latestTag = result?.latest?.tag ?? latestTag;
    const staleShell = shellIsStale(app.getVersion(), latestTag);
    if (result.update_available === true) {
      const choice = await dialog.showMessageBox({type:'question', message:`Engine update available: ${result.latest?.tag || 'new release'}`, detail:'Apply the release using the engine’s update safeguards?', buttons:['Cancel','Apply update'], defaultId:0, cancelId:0});
      if (choice.response === 1) {
        const applied = JSON.parse(await command(['update','--apply','--channel','release','--json','--root',workspace], 300000));
        if (typeof applied.reason !== 'string') throw new Error('The engine returned an update result without a reason.');
        await dialog.showMessageBox({message:'Engine update', detail: applied.reason, buttons:['OK']});
      }
    } else if (staleShell) {
      await dialog.showMessageBox({message:`A newer desktop app is available: ${latestTag}`, detail:`Your engine is up to date, but this application is version ${app.getVersion()}. The engine updates itself; the application does not — download the new build and run its installer to replace it.`, buttons:['OK']});
    } else await dialog.showMessageBox({message:result.latest ? 'Your engine is up to date.' : 'Could not check for updates.', detail:JSON.stringify(result, null, 2)});
    return result;
  } finally { updating = false; }
}
function createWindow() {
  const b = settings.bounds;
  const visible = validBounds(b) && screen.getAllDisplays().some(d => b.x < d.workArea.x + d.workArea.width && b.x + b.width > d.workArea.x && b.y < d.workArea.y + d.workArea.height && b.y + b.height > d.workArea.y);
  const w = new BrowserWindow({width:1200, height:800, ...(visible ? b : {}), minWidth:720, minHeight:520, title:'Pravrudhi', backgroundColor:'#11151b', show:false, icon:path.join(__dirname,'renderer/icon.png'), webPreferences:{preload:path.join(__dirname,'preload.js'), contextIsolation:true, nodeIntegration:false, sandbox:true, offscreen}});
  windows.add(w);
  w.webContents.once('did-finish-load', () => {
    smoke?.launched();
    // Photograph the window when asked. The smoke run proves the app launched and reached an engine; this shows
    // what it actually looks like, which is the only way to check a desktop shell without sitting in front of it.
    const shot = process.env.PRAVRUDHI_DESKTOP_SHOT;
    if (shot && !smokeMode) {
      setTimeout(() => {
        w.webContents.capturePage().then(img => {
          require('node:fs').writeFileSync(shot, img.toPNG());
          console.log(`window captured to ${shot}`);
        }).catch(e => console.error(`capture failed: ${e}`));
      }, 6000);
    }
  });
  w.once('ready-to-show', () => { if (!offscreen) w.show(); if (settings.maximized) w.maximize(); });
  const remember = () => { if (!w.isDestroyed()) { settings.bounds = w.getNormalBounds(); settings.maximized = w.isMaximized(); persist(); } };
  w.on('close', remember); w.on('resize', remember); w.on('move', remember); w.on('closed', () => windows.delete(w));
  const navigate = (event, url) => {
    if (linkPolicy(url,status.origin) === 'internal') return;
    event.preventDefault(); if (linkPolicy(url,status.origin) === 'external') safe(() => shell.openExternal(url))();
  };
  w.webContents.setWindowOpenHandler(({url}) => { if (linkPolicy(url,status.origin) === 'external') safe(() => shell.openExternal(url))(); else if (linkPolicy(url,status.origin) === 'internal') w.loadURL(url).catch(() => {}); return {action:'deny'}; });
  w.webContents.on('will-navigate', navigate); w.webContents.on('will-redirect', navigate);
  w.webContents.on('will-attach-webview', event => event.preventDefault());
  w.webContents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  w.webContents.on('did-fail-load', (_event, code, detail, _url, main) => { if (main && code !== -3) { if (smoke) { finishSmoke(new Error(`Could not load the interface: ${detail}`)); return; } publish({phase:'error',detail:`Could not load the interface: ${detail}`}); w.loadFile(statusFile).catch(() => {}); } });
  if (status.origin) w.loadURL(status.origin).catch(() => {}); else w.loadFile(statusFile).catch(() => {});
  return w;
}
function refreshTray() {
  const state = trayState(status.phase === 'running', engineController, focus, ()=>app.quit(), safe);
  tray?.setToolTip(state.tooltip);
  tray?.setContextMenu(Menu.buildFromTemplate(state.items));
}
async function shutdown() { controller?.abort(); engine = null; await processes.shutdown(); }
const instanceReady = singleInstance(app, focus);
if (instanceReady) {
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', event => { if (!quitting) { event.preventDefault(); quitting = true; shutdown().catch(e => { console.error(e); smokeExitCode = 1; }).finally(() => smoke ? app.exit(smokeExitCode) : app.quit()); } });
  for (const signal of ['SIGINT','SIGTERM']) process.on(signal, () => app.quit());
  app.whenReady().then(async () => {
    stateFile = path.join(app.getPath('userData'), 'desktop-state.json'); settings = readState(stateFile);
    workspace = defaultWorkspace({env:process.env.PRAVRUDHI_WORKSPACE,saved:settings.workspace,binary:await discoverEngine({saved:settings.enginePath}),home:app.getPath('home')}); settings.workspace = workspace;
    const handlers = {'engine:status':() => ({...status,workspace,shellVersion:app.getVersion(),shellStale:shellIsStale(app.getVersion(),latestTag)}), 'engine:locate':locate,'engine:restart':() => serialize(start),'engine:stop':() => serialize(stop),'engine:doctor':doctor,'engine:updates':updates,'engine:workspace':engineController.openWorkspace, 'engine:health':api.health, 'engine:update-state':api.update, 'engine:open':async()=>{ if (!status.origin) throw new Error('Engine is not connected.'); for (const w of windows) await w.loadURL(status.origin); }};
    for (const [channel, handler] of Object.entries(handlers)) ipcMain.handle(channel, (event) => {
      const url = event.senderFrame?.url;
      if (!windows.has(BrowserWindow.fromWebContents(event.sender)) || event.senderFrame !== event.sender.mainFrame || !(url === statusURL || linkPolicy(url,status.origin) === 'internal')) throw new Error('Untrusted desktop request');
      return handler();
    });
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      {label:'File',submenu:[{label:'New Window',accelerator:'CmdOrCtrl+N',click:createWindow},{label:'Locate engine…',click:safe(locate)},{type:'separator'},{role:'quit'}]},
      {role:'editMenu'},
      {label:'View',submenu:[{role:'reload'},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'},{type:'separator'},{role:'toggleDevTools'},{role:'togglefullscreen'}]},
      {label:'Engine',submenu:[...engineMenu(engineController,safe),{label:'Connection and diagnostics',click:statusScreens},{label:'Choose workspace…',click:safe(async () => { const r = await dialog.showOpenDialog({properties:['openDirectory']}); if (!r.canceled) { workspace = r.filePaths[0]; settings.workspace = workspace; delete settings.engineURL; persist(); await serialize(start); } })}]},
      {label:'Help',submenu:[{label:'Documentation',click:safe(() => shell.openExternal(docs))},{label:'About Pravrudhi',click:safe(() => dialog.showMessageBox({message:'Pravrudhi',detail:`Desktop ${app.getVersion()}\nEngine ${status.version}\n${status.binary || 'No engine located'}\nWorkspace: ${workspace}`}))}]}
    ]));
    // A bundled, generated bitmap keeps the tray independent of system icon themes.
    const pixels = Buffer.alloc(24 * 24 * 4);
    for (let y=3;y<21;y++) for(let x=5;x<19;x++) if(x<9 || (y<13 && (y<7 || y>9 || x>14))) { const i=(y*24+x)*4; pixels[i]=168; pixels[i+1]=208; pixels[i+2]=120; pixels[i+3]=255; }
    tray = new Tray(nativeImage.createFromBitmap(pixels,{width:24,height:24})); tray.on('click',focus); refreshTray();
    createWindow(); instanceReady(); serialize(start).catch(e => smoke ? finishSmoke(e) : dialog.showErrorBox('Pravrudhi',e.message));
    app.on('activate',focus);
  }).catch(e => { if (smoke) finishSmoke(e); else { dialog.showErrorBox('Pravrudhi',e.message); app.quit(); } });
}
