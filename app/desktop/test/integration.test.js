'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const {createApiClient} = require('../lib/api');
const {selectConnection} = require('../lib/connection');
const {terminateGroup, singleInstance} = require('../lib/lifecycle');
const {engineMenu, trayState} = require('../lib/menu');
const {createSmokeReporter} = require('../lib/smoke');
const response = value => ({ok:true, json:async()=>value});
test('smoke launcher disables only the process sandbox with Xvfb and headless fallback',async()=>{
  const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
  for (const hasXvfb of [true,false]) {
    const child=new EventEmitter();const processStub=new EventEmitter();processStub.env={};
    let launched,removed=false;
    vm.runInNewContext(fs.readFileSync(require.resolve('../smoke'),'utf8'),{
      __dirname:path.dirname(require.resolve('../smoke')),console,process:processStub,
      setTimeout:()=>1,clearTimeout(){},
      require:name=>({
        'node:child_process':{
          spawnSync:()=>hasXvfb ? {status:0} : {error:Error('ENOENT')},
          spawn:(command,args,options)=>{launched={command,args,options};return child;}
        },
        'node:fs':{rmSync:()=>{removed=true;},readFileSync:()=>JSON.stringify({launched:true,engine_found:true,engine_url:'http://127.0.0.1:8008',page_title:'Engine',health_ok:true,errors:[]})},
        'node:path':path,electron:'electron-binary',
        './lib/smoke':{createSmokeReporter},'./lib/lifecycle':{terminateGroup}
      }[name])
    });
    assert.equal(removed,true);
    assert.equal(launched.command,hasXvfb ? 'xvfb-run' : 'electron-binary');
    assert.ok(launched.args.includes('--no-sandbox'));
    assert.equal(launched.options.env.ELECTRON_ENABLE_LOGGING,'1');
    assert.equal(launched.options.env.PRAVRUDHI_DESKTOP_SMOKE,'1');
    if (hasXvfb) assert.deepEqual(Array.from(launched.args.slice(0,2)),['-a','electron-binary']);
    else {
      assert.equal(launched.options.env.ELECTRON_DISABLE_GPU,'1');
      assert.ok(launched.args.includes('--headless'));
    }
    child.emit('close',0,null);
    assert.equal(processStub.exitCode,0);
  }
});
// Was a test about backlog and pending sign-off counts. Both are the operator's surfaces and the product no
// longer reaches them, so this asks the same questions of the routes a user actually has: the method is fixed,
// the endpoint is the one named, and the response is returned as it stands.
test('API calls the endpoint it names, with the method it declared', async()=>{
  const seen=[];
  const api=createApiClient(()=> 'http://127.0.0.1:8008', {fetchFn:async(url,options)=>{
    seen.push(new URL(url).pathname); assert.equal(options.method,'GET');
    return response({'/api/health':{ok:true,version:'installed'},
      '/api/update':{current:{version:'installed'},latest:null,update_available:false},
      '/api/workspaces':{workspaces:[{slug:'mine'}]},
      '/api/objectives':{objectives:[]}}[new URL(url).pathname]);
  }});
  assert.equal((await api.health()).version,'installed');
  assert.equal((await api.update()).current.version,'installed');
  assert.deepEqual((await api.workspaces()).workspaces,[{slug:'mine'}]);
  assert.deepEqual(seen,['/api/health','/api/update','/api/workspaces']);
});
test('API errors name the endpoint they happened on',async()=>{
  for(const fetchFn of [async()=>({ok:false,status:500}), async()=>{throw Error('ECONNREFUSED');},async()=>({ok:true,json:async()=>{throw Error('invalid JSON');}})]) {
    await assert.rejects(createApiClient(()=> 'http://127.0.0.1:8008',{fetchFn}).health(),/\/api\/health/);
  }
  await assert.rejects(createApiClient(()=>null).health(),/not connected/);
  // Was backlog() and inbox(), both now the operator's. What is left worth asserting here is that a transport
  // failure still names the endpoint it happened on, which is the part that makes a report actionable.
});
test('attach selects a healthy existing engine without allocating a port or requiring a binary',async()=>{
  const result=await selectConnection({candidates:['http://127.0.0.1:8008'],health:async()=>({ok:true}),discover:async()=>null,allocate:()=>assert.fail('must not spawn')});
  assert.equal(result.attached,true); assert.equal(result.origin,'http://127.0.0.1:8008');
});
test('spawn is selected after failed attach; unhealthy or remote addresses are never attached',async()=>{
  const checked=[];
  const result=await selectConnection({candidates:['https://example.com','http://127.0.0.1:8008'],health:async url=>{checked.push(url);throw Error('refused');},discover:async()=> 'bin/pravrudhi',allocate:async()=> 12345});
  assert.deepEqual(checked,['http://127.0.0.1:8008']);assert.equal(result.attached,false);assert.equal(result.binary,'bin/pravrudhi');assert.equal(result.origin,'http://127.0.0.1:12345');
  await assert.rejects(selectConnection({candidates:[],discover:async()=>null}),/installed engine/);
});
test('every Engine menu action invokes its controller and tray reflects state',async()=>{
  const calls=[];const controller=Object.fromEntries(['restart','stop','checkForUpdates','openWorkspace'].map(k=>[k,async()=>calls.push(k)]));
  const items=engineMenu(controller);
  for(const item of items) await item.click();
  assert.deepEqual(calls,['restart','stop','checkForUpdates','openWorkspace']);
  for(const running of [true,false]) {
    const tray=trayState(running,controller,()=>calls.push('focus'),()=>calls.push('quit'));
    assert.match(tray.tooltip,new RegExp(running?'running':'stopped'));
    assert.equal(tray.items[0].enabled,false);
    for(const item of tray.items.slice(1)) await item.click();
  }
  assert.deepEqual(calls.slice(4),['focus','restart','stop','quit','focus','restart','stop','quit']);
});
test('second launch quits without initialization; primary restores focus including an early second launch',()=>{
  const app=new EventEmitter();let quits=0,focus=0;
  app.quit=()=>quits++;app.requestSingleInstanceLock=()=>false;
  assert.equal(singleInstance(app,()=>focus++),null);assert.equal(quits,1);
  app.requestSingleInstanceLock=()=>true;const ready=singleInstance(app,()=>focus++);
  app.emit('second-instance');assert.equal(focus,0);ready();assert.equal(focus,1);
  app.emit('second-instance');assert.equal(focus,2);
});
test('teardown signals the entire process group and escalates; missing groups are harmless',async()=>{
  const calls=[];await terminateGroup({pid:42},{kill:(...args)=>calls.push(args),sleep:async()=>{}});
  assert.deepEqual(calls,[[-42,'SIGTERM'],[-42,'SIGKILL']]);
  await terminateGroup({pid:42},{kill:()=>{throw Object.assign(Error('gone'),{code:'ESRCH'});},sleep:async()=>{}});
  await terminateGroup({}, {kill:()=>assert.fail('no pid'),sleep:async()=>{}});
  await assert.rejects(terminateGroup({pid:42},{kill:()=>{throw Object.assign(Error('denied'),{code:'EPERM'});}}),/denied/);
});
test('smoke records observed values and rejects failures rather than manufacturing success',async()=>{
  let saved;const make=()=>createSmokeReporter('report.json',{write:(_file,value)=>{saved=value;}});
  const reporter=make(); reporter.launched();reporter.engine('http://127.0.0.1:8008');
  assert.equal(await reporter.finish({getTitle:()=> 'Real engine title',health:async()=>({ok:true})}),0);
  assert.deepEqual(saved,{launched:true,engine_found:true,engine_url:'http://127.0.0.1:8008',page_title:'Real engine title',health_ok:true,edition:null,errors:[]});
  // The edition the app actually ran as, carried through so the packaged smoke can refuse a Studio build that
  // came back as the product. Recorded, never inferred: an absent edition stays null rather than becoming one.
  const stamped=createSmokeReporter('report.json',{write:(_f,v)=>{saved=v;},edition:'studio'});
  stamped.launched();stamped.engine('http://127.0.0.1:8008');
  assert.equal(await stamped.finish({getTitle:()=>'Studio',health:async()=>({ok:true})}),0);
  assert.equal(saved.edition,'studio');
  for(const health of [async()=>({ok:false}),async()=>{throw Error('refused');}]) {
    const r=make();r.launched();r.engine('http://127.0.0.1:8008');assert.equal(await r.finish({getTitle:()=> 'Page',health}),1);assert.ok(saved.errors.length);assert.equal(saved.health_ok,false);
  }
  assert.equal(await make().fail(Error('load failed')),1);assert.equal(saved.launched,false);assert.deepEqual(saved.errors,['load failed']);
  const empty=make();assert.equal(await empty.finish({getTitle:()=>'',health:async()=>({ok:true})}),1);assert.ok(saved.errors.length);
  await assert.rejects(createSmokeReporter('bad',{write:()=>{throw Error('disk full');}}).fail(Error('failed')),/disk full/);
});
test('smoke writes parseable JSON to disk and preserves partial observations on failure',async t=>{
  const fs=require('node:fs/promises'); const path=require('node:path');
  const dir=await fs.mkdtemp(path.join(__dirname,'.smoke-test-'));t.after(()=>fs.rm(dir,{recursive:true,force:true}));
  const file=path.join(dir,'report.json');const reporter=createSmokeReporter(file);
  reporter.launched(); reporter.engine('http://127.0.0.1:8008');await reporter.fail(Error('frontend missing'));
  const report=JSON.parse(await fs.readFile(file,'utf8'));
  assert.equal(report.launched,true);assert.equal(report.engine_found,true);assert.equal(report.health_ok,false);assert.deepEqual(report.errors,['frontend missing']);
});
test('sandbox preload provides an enumerated invoke-only API with no renderer-controlled arguments',async()=>{
  const fs=require('node:fs');const vm=require('node:vm');let exposed;const channels=[];
  vm.runInNewContext(fs.readFileSync(require.resolve('../preload'),'utf8'),{require:name=>{
    assert.equal(name,'electron');return {contextBridge:{exposeInMainWorld:(name,api)=>{assert.equal(name,'desktop');exposed=api;}},ipcRenderer:{invoke:async(...args)=>{assert.equal(args.length,1);channels.push(args[0]);}}};
  }});
  assert.ok(Object.isFrozen(exposed));
  for(const fn of Object.values(exposed)) await fn('untrusted arbitrary command');
  // engine:backlog and engine:inbox are gone with the operator surfaces they fed. They were still wired in
  // main.js and preload.js after the client dropped them, so every launch died on "handler is not a function".
  assert.deepEqual(channels,['engine:health','engine:update-state','engine:open','engine:status','engine:locate','engine:restart','engine:stop','engine:doctor','engine:updates','engine:workspace']);
});
test('first-run renderer displays API data and named failed doctor reasons with copyable commands',async()=>{
  const fs=require('node:fs');const vm=require('node:vm');const elements=new Map();
  const element=()=>({textContent:'',dataset:{},children:[],addEventListener(){},replaceChildren(){this.children=[];},append(...children){this.children.push(...children);}});
  const body=element();let reads=0;
  const document={body,getElementById:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element};
  const desktop={engineStatus:async()=>({phase:'running',origin:'http://127.0.0.1:8008',binary:'engine',workspace:'workspace',checks:[{name:'docker',ok:false,detail:'Docker daemon is not running',recovery:{command:'systemctl start docker',label:'Copy command'}}]}),health:async()=>{reads++;return {ok:true,version:'test-installed'};},updateState:async()=>{reads++;return {current:{version:'test-installed'},latest:{tag:'next'},update_available:true};}};
  vm.runInNewContext(fs.readFileSync(require.resolve('../renderer/app'),'utf8'),{document,window:{desktop},setTimeout:()=>{}});
  await new Promise(resolve=>setImmediate(resolve));
  // Two reads, not four: the backlog and pending sign-off tiles were operator surfaces and are gone.
  assert.equal(reads,2);assert.match(elements.get('health-value').textContent,/Healthy.*test-installed/);assert.match(elements.get('update-value').textContent,/Update available: next/);

  const check=elements.get('checks').children[0];assert.match(check.children[0].textContent,/docker/);assert.match(check.children[1].textContent,/daemon is not running/);
  assert.equal(check.children[2].children[0].textContent,'systemctl start docker');assert.equal(body.dataset.apiReady,'true');
});
test('process ownership spawns detached without a shell and tears down only its own groups once',async()=>{
  const {createProcessOwner}=require('../lib/lifecycle');const signalled=[];let options;
  const owner=createProcessOwner({spawn:(_bin,_args,opts)=>{options=opts;return {pid:42};},terminate:async child=>{signalled.push(child.pid);}});
  const child=owner.launch('engine',['app'],{cwd:'workspace'});
  assert.equal(options.detached,true);assert.equal(options.shell,false);assert.equal(options.cwd,'workspace');
  await owner.stop({pid:99});assert.deepEqual(signalled,[]);
  await Promise.all([owner.stop(child),owner.stop(child)]);assert.deepEqual(signalled,[42]);
  owner.launch('engine',['doctor'],{cwd:'workspace'});await owner.shutdown();assert.deepEqual(signalled,[42,42]);
  assert.throws(()=>owner.launch('engine',[]),/shutting down/);
});
test('focus restores a minimized primary window, or creates a window when none remain',()=>{
  const {focusWindow}=require('../lib/lifecycle');const calls=[];
  const window={isMinimized:()=>true,restore:()=>calls.push('restore'),show:()=>calls.push('show'),focus:()=>calls.push('focus')};
  focusWindow(new Set([window]),()=>assert.fail('already exists'));assert.deepEqual(calls,['restore','show','focus']);
  let created=false;focusWindow(new Set(),()=>{created=true;return window;});assert.equal(created,true);
});
test('default workspace respects the user and detects a source installation with its built frontend',()=>{
  const {defaultWorkspace}=require('../lib/connection');const path=require('node:path');
  assert.equal(defaultWorkspace({env:'selected',saved:'saved',home:'home'}),path.resolve('selected'));
  assert.equal(defaultWorkspace({saved:'saved',home:'home'}),path.resolve('saved'));
  assert.equal(defaultWorkspace({binary:'project/.venv/bin/pravrudhi',home:'home',exists:()=>true}),path.resolve('project'));
  // Falling back to the bare home directory made the engine write .pravrudhi/ and research/ straight into it.
  // A dedicated directory is used instead, and a release install beside it is preferred when one exists.
  assert.equal(
    defaultWorkspace({binary:'project/.venv/bin/pravrudhi',home:'home',exists:()=>false}),
    path.resolve('home/pravrudhi'),
  );
  assert.equal(
    defaultWorkspace({home:'home',exists:p=>String(p).includes('pravrudhi-release')}),
    path.resolve('home/pravrudhi-release'),
  );
});
test('main bootstrap attaches, runs doctor, loads the engine and reports before tearing down',async()=>{
  const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
  const desktopDir=path.dirname(require.resolve('../main'));
  const app=new EventEmitter();let report,windowOptions,exited,spawnedApp=false,shutdown=false;
  const exit=new Promise(resolve=>{exited=resolve;});
  Object.assign(app,{requestSingleInstanceLock:()=>true,setPath(){},getPath:()=>desktopDir,getVersion:()=> 'test-shell',whenReady:async()=>{},quit:()=>app.emit('before-quit',{preventDefault(){}}),exit:code=>exited(code)});
  class Window extends EventEmitter {
    constructor(options) {
      super();windowOptions=options;this.webContents=new EventEmitter();
      Object.assign(this.webContents,{session:{setPermissionRequestHandler(){}},setWindowOpenHandler(){},executeJavaScript:async script=>assert.match(script,/apiReady/),getTitle:()=>this.title});
    }
    async loadFile(){this.title='Desktop fixture';this.webContents.emit('did-finish-load');}
    async loadURL(url){assert.equal(url,'http://127.0.0.1:8008');this.title='Engine fixture';this.webContents.emit('did-finish-load');}
  }
  class Tray { on(){} setToolTip(){} setContextMenu(){} }
  const core=require('../lib/core');const lifecycle=require('../lib/lifecycle');
  const modules={
    electron:{app,BrowserWindow:Window,Menu:{buildFromTemplate:items=>items,setApplicationMenu(){}},Tray,nativeImage:{createFromBitmap(){}},ipcMain:{handle(){}},dialog:{showErrorBox:(_title,message)=>assert.fail(message)},shell:{},screen:{getAllDisplays:()=>[]}},
    './lib/core':{...core,discoverEngine:async()=> 'fixture-engine',pollHealth:async()=>({ok:true}),readState:()=>({}),writeState(){}},
    './lib/connection':{selectConnection:async()=>({attached:true,binary:'fixture-engine',origin:'http://127.0.0.1:8008'}),defaultWorkspace:()=>desktopDir},
    './lib/api':{createApiClient:()=>({health:async()=>({ok:true,version:'fixture'})})},
    './lib/smoke':{createSmokeReporter:file=>createSmokeReporter(file,{write:(_file,value)=>{report=value;}})},
    './lib/lifecycle':{...lifecycle,createProcessOwner:()=>({launch:(_binary,args)=>{
      if(args[0]==='app')spawnedApp=true;
      assert.equal(args[0],'doctor');const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();
      queueMicrotask(()=>{child.stdout.emit('data',JSON.stringify({checks:[{name:'docker',ok:false,detail:'fixture reason'}]}));child.emit('close',1);});return child;
    },stop:async()=>{},shutdown:async()=>{shutdown=true;}})}
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../main'),'utf8'),{
    require:name=>modules[name] || (name.startsWith('./') ? require(path.join(desktopDir,name)) : require(name)),
    __dirname:desktopDir,process:{env:{PRAVRUDHI_DESKTOP_SMOKE:'1'},on(){}},console,Buffer,AbortController,AbortSignal,URL,setTimeout,clearTimeout,
    fetch:async()=>({ok:true,headers:{get:()=> 'text/html'}})
  });
  assert.equal(await exit,0);assert.equal(shutdown,true);assert.equal(spawnedApp,false);
  assert.equal(windowOptions.webPreferences.contextIsolation,true);assert.equal(windowOptions.webPreferences.nodeIntegration,false);assert.equal(windowOptions.webPreferences.sandbox,true);
  assert.equal(report.page_title,'Engine fixture');assert.equal(report.launched,true);assert.equal(report.health_ok,true);assert.deepEqual(report.errors,[]);
});

test('the product never reaches an operator surface', async () => {
  // The engine splits its routes into the operator's and the product's in src/pravrudhi/api/roles.py, and the
  // desktop application is the product. Naming the forbidden paths here rather than trusting review means a
  // route added to the client in a hurry fails a test instead of quietly exposing the engine's own internals
  // in something a user installed.
  const source = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'lib', 'api.js'), 'utf8');
  // /api/runs and /api/models are absent from this list on purpose: they became the product's once each
  // project got its own run manager. Before that they spent the operator's hardware under the operator's keys
  // whoever asked, which is why they were listed here.
  const operatorOnly = ['/api/nights', '/api/candidates', '/api/observations', '/api/search', '/api/swarm',
    '/api/inbox', '/api/jobs', '/api/diffs', '/api/agents', '/api/fleet', '/api/hosts', '/api/parity',
    '/api/appetite', '/api/heartbeat', '/api/svasthya', '/api/requests', '/api/sandboxes', '/api/external',
    '/api/benchmarks', '/api/health-state', '/api/update/apply', '/api/update/rollback'];
  const found = operatorOnly.filter(path => source.includes(`'${path}'`));
  assert.deepEqual(found, [], `the desktop client reaches operator-only routes: ${found.join(', ')}`);
});

test('every test file in this directory is actually run', () => {
  // `npm test` runs `node --test test/`, which on this Node version loads test/index.js rather than discovering
  // files. Two suites were added without a line in that file and sat there unexecuted: the count did not move,
  // and the tasks that wrote them passed their own validation on tests nobody had run. A file that is never
  // required is worse than no file, because it looks like coverage.
  const fs = require('node:fs'), path = require('node:path');
  const dir = __dirname;
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.test.js')).sort();
  const index = fs.readFileSync(path.join(dir, 'index.js'), 'utf8');
  const missing = files.filter(f => !index.includes(`./${f}`));
  assert.deepEqual(missing, [], `these test files are never required by test/index.js: ${missing.join(', ')}`);
});
