'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {EventEmitter} = require('node:events');
const {discoverEngine,freePort,pollHealth,parseDoctor,linkPolicy,readState,writeState,validBounds} = require('../lib/core');
const {recovery} = require('../lib/recovery');
test('discovery respects every priority and falls through invalid candidates', async () => {
  const home = path.resolve('test-home');
  const candidates = ['override/bin','path-one/pravrudhi','path-two/pravrudhi',path.join(home,'pravrudhi-release/.pravrudhi/releases/current/.venv/bin/pravrudhi'),path.join(home,'.local/bin/pravrudhi'),'chosen/engine'];
  for(let first=0;first<candidates.length;first++) {
    const seen=[];
    const actual = await discoverEngine({env:{PRAVRUDHI_BIN:candidates[0],PATH:['path-one','path-two'].join(path.delimiter)},home,saved:candidates[5],executable:async p => {seen.push(p); return candidates.indexOf(p)>=first;}});
    assert.equal(actual,path.resolve(candidates[first])); assert.deepEqual(seen,candidates.slice(0,first+1));
  }
  assert.equal(await discoverEngine({env:{},home,executable:async()=>false}),null);
});
test('discovery skips directories and non-executable files on disk', async t => {
  const home=await fs.mkdtemp(path.join(os.tmpdir(),'desktop-discovery-')); t.after(()=>fs.rm(home,{recursive:true,force:true}));
  const bad=path.join(home,'bad'), good=path.join(home,'engine'); await fs.writeFile(bad,'x',{mode:0o600}); await fs.writeFile(good,'#!/bin/sh\n',{mode:0o700});
  assert.equal(await discoverEngine({env:{PRAVRUDHI_BIN:bad,PATH:home},home,saved:good}),good);
  assert.equal(await discoverEngine({env:{PRAVRUDHI_BIN:home},home,saved:good}),good);
});
test('Finder launch discovers the Mac release without shell PATH configuration', async () => {
  const home = '/Users/sharath';
  const binary = path.join(home, 'pravrudhi/.pravrudhi/releases/current/.venv/bin/pravrudhi');
  assert.equal(await discoverEngine({home, env:{PATH:'/usr/bin:/bin'}, executable:async p => p === binary}), binary);
});
test('discovery resolves to an absolute path independent of the packaged AppImage mount and its cwd', async t => {
  // A double-clicked AppImage can run with its cwd set to its own extracted mount
  // point (e.g. /tmp/.mount_XXXXXX) rather than the user's shell directory. Discovery
  // must still walk PRAVRUDHI_BIN, PATH, the release venv, ~/.local/bin, then a saved
  // path and land on an absolute result, none of it resolved relative to that mount.
  const originalCwd = process.cwd();
  const mountDir = await fs.mkdtemp(path.join(os.tmpdir(), 'mount-pravrudhi-'));
  t.after(async () => { process.chdir(originalCwd); await fs.rm(mountDir, {recursive: true, force: true}); });
  process.chdir(mountDir);
  const home = path.resolve('appimage-home');
  const winner = path.join(home, '.local/bin/pravrudhi');
  const seen = [];
  const found = await discoverEngine({
    env: {PATH: '/usr/bin'},
    home, saved: path.join(home, 'chosen/pravrudhi'),
    executable: async p => { seen.push(p); return p === winner; }
  });
  assert.equal(found, winner);
  assert.ok(path.isAbsolute(found));
  assert.equal(found, path.resolve(winner));
  assert.ok(seen.includes(path.join(home, 'pravrudhi-release/.pravrudhi/releases/current/.venv/bin/pravrudhi')));
  assert.ok(seen.includes(winner));
});
test('free-port selection binds port zero on loopback and closes before returning', async () => {
  const server = new EventEmitter(); let closed = false;
  server.listen = (port, host, callback) => { assert.equal(port,0); assert.equal(host,'127.0.0.1'); callback(); };
  server.address = () => ({port:49152}); server.close = callback => { closed = true; callback(); };
  assert.equal(await freePort(() => server),49152); assert.equal(closed,true);
  server.listen = () => queueMicrotask(() => server.emit('error',new Error('bind failed')));
  await assert.rejects(freePort(() => server),/bind failed/);
});
test('selected port can be bound on loopback when the environment permits sockets', async t => {
  let port; try { port=await freePort(); } catch(e) { if (e.code === 'EPERM' || e.code === 'EACCES') { t.skip('Sandbox prohibits loopback sockets; selection contract covered with an injected server.'); return; } throw e; } assert.ok(Number.isInteger(port) && port>0 && port<=65535);
  const server=net.createServer(); await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});
  await new Promise(resolve=>server.close(resolve));
});
test('health retries a failed fetch and an unhealthy response before succeeding',async()=>{
  let calls=0,clock=0;
  const health=await pollHealth('http://localhost/api/health',{now:()=>clock,sleep:async ms=>{clock+=ms;},fetchFn:async()=>{calls++;if(calls===1)throw Error('refused');return {ok:true,json:async()=>({ok:calls===3})};}});
  assert.equal(health.ok,true);assert.equal(calls,3);
});
test('health timeout is bounded and does not accept ok false',async()=>{
  let clock=0,calls=0;
  await assert.rejects(pollHealth('unused',{timeout:1000,interval:250,now:()=>clock,sleep:async ms=>{clock+=ms;},fetchFn:async()=>{calls++;return {ok:true,json:async()=>({ok:false})};}}),/30 seconds/);
  assert.equal(clock,1000);assert.equal(calls,4);
});
test('a fetch that ignores its abort signal still times out',async()=>{
  await assert.rejects(pollHealth('unused',{timeout:20,fetchFn:()=>new Promise(()=>{})}),/healthy/);
});
test('health supports cancellation',async()=>{
  const controller=new AbortController();controller.abort();await assert.rejects(pollHealth('unused',{signal:controller.signal}),{name:'AbortError'});
});
test('doctor parses real-shaped JSON even when overall readiness fails',()=>{
  const checks=[{name:'initialised',ok:true,detail:'Config and ledger exist.'},{name:'docker',ok:false,detail:"Docker binary not installed: 'docker' executable is missing from PATH."},{name:'gpu',ok:true,detail:"No GPU detected: 'nvidia-smi' is not on PATH."}];
  assert.deepEqual(parseDoctor(JSON.stringify({ok:false,checks})),checks);
  for(const invalid of ['not json','{}','{"checks":[{"name":"docker","ok":"yes","detail":"x"}]}']) assert.throws(()=>parseDoctor(invalid));
});
test('navigation distinguishes engine origin, external links, and prohibited schemes',()=>{
  const origin='http://127.0.0.1:8008';
  assert.equal(linkPolicy('https://example.com',origin),'external');assert.equal(linkPolicy(`${origin}/runs`,origin),'internal');
  for(const url of ['javascript:alert(1)','file:///etc/passwd','data:text/html,test','broken']) assert.equal(linkPolicy(url,origin),'deny');
  assert.equal(linkPolicy('http://127.0.0.1:9000',origin),'external');assert.equal(linkPolicy('https://127.0.0.1:8008',origin),'external');
});
test('window bounds and remembered engine survive atomic persistence',async t=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'desktop-state-'));t.after(()=>fs.rm(dir,{recursive:true,force:true}));
  const file=path.join(dir,'state.json');assert.deepEqual(readState(file),{});
  const state={bounds:{x:-100,y:40,width:1200,height:800},maximized:true,enginePath:'chosen/pravrudhi'};
  writeState(file,state);assert.deepEqual(readState(file),state);assert.ok(validBounds(readState(file).bounds));
  assert.ok(!validBounds({...state.bounds,width:0}));await fs.writeFile(file,'corrupt');assert.deepEqual(readState(file),{});
});
test('recovery quotes paths and does not offer destructive ledger repair',()=>{
  const init=recovery({name:'initialised',ok:false,detail:'Missing config'},"bin/engine's",'my workspace');
  assert.match(init.command,/'\\''/);assert.match(init.command,/init --root 'my workspace'/);
  const ledger=recovery({name:'ledger',ok:false,detail:'Ledger verification failed: hash mismatch'},'engine','.');
  assert.match(ledger.note,/no safe automatic repair/);assert.match(ledger.command,/doctor --json/);
});
