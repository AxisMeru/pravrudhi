'use strict';
async function terminateGroup(child, {kill = process.kill.bind(process), sleep = ms => new Promise(resolve=>setTimeout(resolve,ms)), grace = 800} = {}) {
  if (!child?.pid) return;
  const signal = value => {
    try { kill(-child.pid,value); return true; }
    catch (error) { if (error.code === 'ESRCH') return false; throw error; }
  };
  if (!signal('SIGTERM')) return;
  await sleep(grace);
  signal('SIGKILL');
}
function singleInstance(app, focus) {
  if (!app.requestSingleInstanceLock()) { app.quit(); return null; }
  let ready = false, pending = false;
  app.on('second-instance',()=> { if (ready) focus(); else pending = true; });
  return () => { ready = true; if (pending) { pending = false; focus(); } };
}
function focusWindow(windows, createWindow) {
  const window = [...windows][0] || createWindow();
  if (window.isMinimized()) window.restore();
  window.show(); window.focus();
}
function createProcessOwner({spawn = require('node:child_process').spawn, terminate = terminateGroup} = {}) {
  const owned = new Set(), stopping = new Map();
  let shuttingDown = false;
  function stop(child) {
    if (stopping.has(child)) return stopping.get(child);
    if (!owned.has(child)) return Promise.resolve();
    const promise = Promise.resolve().then(()=>terminate(child)).then(()=>owned.delete(child)).finally(()=>stopping.delete(child));
    stopping.set(child,promise);
    return promise;
  }
  return {
    launch(binary,args,options = {}) {
      if (shuttingDown) throw new Error('Application is shutting down.');
      const child = spawn(binary,args,{...options,detached:true,shell:false,stdio:['ignore','pipe','pipe']});
      owned.add(child); return child;
    },
    stop,
    async shutdown() {
      shuttingDown = true;
      const results = await Promise.allSettled([...owned].map(stop));
      const failures = results.filter(result=>result.status === 'rejected');
      if (failures.length) throw new AggregateError(failures.map(result=>result.reason),'Could not terminate owned engine processes.');
    }
  };
}
async function restartForUpdate(app, processOwner, {relaunch = a => a.relaunch(), exit = a => a.exit(0), beforeRestart} = {}) {
  if (typeof beforeRestart === 'function') {
    try { await beforeRestart(); }
    catch { /* losing the exact window position must not block a restart the user asked for */ }
  }
  await processOwner.shutdown(); // rejects (and skips relaunch) if the engine could not be confirmed stopped
  relaunch(app);
  exit(app);
}
module.exports = {terminateGroup, singleInstance, focusWindow, createProcessOwner, restartForUpdate};
