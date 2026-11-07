import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { writeFile, readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';

const executable = process.argv[2];
const output = process.argv[3];
if (!executable || !output) throw new Error('Usage: node packaged_smoke.mjs EXE OUTPUT_PREFIX');
const listener = createServer();
await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const child = spawn(executable, process.env.FH1_SMOKE_SCRIPT ? [process.env.FH1_SMOKE_SCRIPT] : [], { windowsHide: true, stdio: 'ignore', env: {
  ...process.env,
  ...(process.env.FH1_SMOKE_APPDATA ? { APPDATA: process.env.FH1_SMOKE_APPDATA } : {}),
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-address=127.0.0.1 --remote-debugging-port=${port}`
} });
let socket;
const pending = new Map();
let serial = 0;
let pauseWaiter;
try {
  let target;
  for (let attempt = 0; attempt < 120; attempt++) {
    if (child.exitCode !== null) throw new Error(`Desktop app exited: ${child.exitCode}`);
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      target = targets.find(item => item.type === 'page' && item.url.startsWith('http://127.0.0.1:'));
      if (target) break;
    } catch {}
    await delay(250);
  }
  if (!target) throw new Error('No native WebView2 page was available');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  socket.addEventListener('message', event => {
    const reply = JSON.parse(event.data);
    if (reply.method === 'Debugger.paused' && pauseWaiter) {const resolve=pauseWaiter;pauseWaiter=null;resolve(reply.params);}
    if (!reply.id) return;
    const callbacks = pending.get(reply.id);
    if (!callbacks) return;
    pending.delete(reply.id);
    reply.error ? callbacks.reject(new Error(JSON.stringify(reply.error))) : callbacks.resolve(reply.result);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
    setTimeout(() => { if (pending.delete(id)) reject(new Error('CDP timeout: ' + method)); }, method === 'Page.captureScreenshot' ? 45000 : 15000).unref();
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  let ui;
  for (let attempt = 0; attempt < 160; attempt++) {
    ui = await evaluate(`({title:document.title,status:document.querySelector('#status')?.textContent,errors:window.editorErrors||[],settings:document.querySelector('#tab-settings')?.hidden===false,cars:document.querySelectorAll('.car-row').length,api:window.pywebview?.api?Object.keys(window.pywebview.api):[]})`);
    if (ui.errors.length) throw new Error(JSON.stringify(ui));
    if (ui.api.includes('choose_path') && (ui.settings || ui.cars > 0)) break;
    await delay(250);
  }
  if (!ui.api.includes('choose_path') || !(ui.settings || ui.cars > 0)) throw new Error('Desktop UI did not initialize: ' + JSON.stringify(ui));
  if (process.env.FH1_SMOKE_COPY_ERROR === '1') {
    const copied = await evaluate(`(()=>{const status=document.querySelector('#status');status.textContent='Missing game file: media/db/gamedb.slt';const range=document.createRange();range.selectNodeContents(status);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);const event=new KeyboardEvent('keydown',{key:'c',ctrlKey:true,bubbles:true,cancelable:true});document.body.dispatchEvent(event);return {text:status.textContent,prevented:event.defaultPrevented};})()`);
    if (copied.text !== 'Missing game file: media/db/gamedb.slt' || copied.prevented) throw new Error('Copying selected error text triggered Copy part: ' + JSON.stringify(copied));
    await evaluate(`document.querySelector('#btnSettings').click();document.querySelector('#cfgGame').value='C:/FH1-editor-test-path-does-not-exist';document.querySelector('#applySettings').click()`);
    let error;
    for (let attempt = 0; attempt < 80; attempt++) {
      error = await evaluate(`document.querySelector('#errorText')?.value`);
      if (error?.includes('not found')) break;
      await delay(100);
    }
    if (!error?.includes('not found')) throw new Error('The actual path error was not kept in the error panel');
    await evaluate(`document.querySelector('#copyError').click();document.querySelector('#status').textContent='ready'`);
    for (let attempt = 0; attempt < 80; attempt++) {
      if (await evaluate(`document.querySelector('#copyError').textContent === 'Copied'`)) break;
      await delay(100);
    }
    const banner = await evaluate(`({visible:!document.querySelector('#errorBanner').hidden,text:document.querySelector('#errorText').value,button:document.querySelector('#copyError').textContent})`);
    if (!banner.visible || banner.text !== error || banner.button !== 'Copied') throw new Error('Error panel/copy button failed: ' + JSON.stringify(banner));
    ui.copyErrorText = true;
    ui.persistentErrorAndCopyButton = true;
  }
  if (process.env.FH1_SMOKE_GAME) {
    await evaluate(`document.querySelector('#btnSettings').click();document.querySelector('#cfgGame').value=${JSON.stringify(process.env.FH1_SMOKE_GAME)};document.querySelector('#cfgDlc').value=${JSON.stringify(process.env.FH1_SMOKE_DLC || '')};document.querySelector('#applySettings').click()`);
    for (let attempt = 0; attempt < 480; attempt++) {
      if (await evaluate('document.querySelectorAll(".car-row").length > 176')) break;
      await delay(250);
    }
    const cars = await evaluate('document.querySelectorAll(".car-row").length');
    if (process.env.FH1_SMOKE_DLC_AS_GAME === '1') {
      const wrong = process.env.FH1_SMOKE_DLC.replace(/[\\/]00000002[\\/]?$/, '');
      await evaluate(`document.querySelector('#btnSettings').click();document.querySelector('#cfgGame').value=${JSON.stringify(wrong)};document.querySelector('#cfgDlc').value='';document.querySelector('#applySettings').click()`);
      let corrected;
      for (let attempt = 0; attempt < 240; attempt++) {
        corrected = await evaluate(`({game:document.querySelector('#cfgGame').value,dlc:document.querySelector('#cfgDlc').value,settings:document.querySelector('#tab-settings').hidden===false,error:!document.querySelector('#errorBanner').hidden})`);
        if (corrected.game !== wrong && !corrected.settings) break;
        await delay(100);
      }
      if (corrected.game === wrong || corrected.settings || corrected.error || !corrected.dlc.split(String.fromCharCode(92)).join('/').endsWith('4D5309C9/00000002')) throw new Error('Xenia DLC folder correction failed: '+JSON.stringify(corrected));
      ui.xeniaDlcFolderCorrected = true;
    }
    if (cars <= 176) throw new Error('Combined game/DLC indexing failed: ' + cars);
    await evaluate(`document.querySelector('#carSearch').value=${JSON.stringify(process.env.FH1_SMOKE_CAR || 'ALF_8C')};document.querySelector('#carSearch').dispatchEvent(new Event('input'));document.querySelector('.car-row').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:1100,clientY:350}));document.querySelector('#carContextMenu button').click()`);
    for (let attempt = 0; attempt < 160; attempt++) {
      if (await evaluate('document.querySelectorAll("#tree .sec").length > 0 && /parts$/.test(document.querySelector("#status").textContent)')) break;
      await delay(250);
    }
    const parts = await evaluate('document.querySelectorAll("#tree .sec").length');
    if (!parts) throw new Error('Packaged car import failed');
    if (process.env.FH1_SMOKE_GIZMO === '1') {
      const summary = await evaluate(`fetch('/api/summary').then(r=>r.json())`);
      const section = summary.sections.find(s => s.name.toLowerCase() === 'body');
      await evaluate(`document.querySelector('#tree .sec[data-si="${section.index}"] .row').click()`);
      const stable = async undo => {
        for (let i=0;i<150;i++) {
          if (await evaluate(`fetch('/api/summary').then(r=>r.json()).then(s=>s.undo===${undo}&&!document.querySelector('#btnUndo').disabled)`)) return;
          await delay(100);
        }
        throw new Error('Gizmo edit did not finish: expected undo '+undo+'; '+await evaluate(`document.querySelector('#status').textContent`));
      };
      const hash = () => evaluate(`(async()=>{const b=await fetch('/api/save').then(r=>r.arrayBuffer());return [...new Uint8Array(await crypto.subtle.digest('SHA-256',b))].map(v=>v.toString(16).padStart(2,'0')).join('')})()`);
      await send('Debugger.enable');
      const source=await readFile(new URL('../toolkit/carbin_editor/app.js',import.meta.url),'utf8');
      const lineNumber=source.split('\n').findIndex(line=>line.includes('const imported = !!place.group && tc.object === place.group;'));
      if(lineNumber<0)throw new Error('Cannot locate gizmo UI for native debugging');
      const handle = async (mode,axis) => {
        const bp=await send('Debugger.setBreakpointByUrl',{urlRegex:'/app\\.js$',lineNumber});
        const paused=new Promise(resolve=>{pauseWaiter=resolve});
        const click=evaluate(`document.querySelector('#transformTools [data-gizmo-mode="${mode}"]').click()`);
        try {
          const frame=await Promise.race([paused,delay(8000).then(()=>{throw new Error('Gizmo debugger did not pause')})]);
          const projected=await send('Debugger.evaluateOnCallFrame',{callFrameId:frame.callFrames[0].callFrameId,returnByValue:true,expression:`(()=>{
            tc.updateMatrixWorld(true); camera.updateMatrixWorld(true);
            const rect=canvas.getBoundingClientRect(),picker=tc._gizmo.picker[tc.mode],ray=new THREE.Raycaster();
            const screen=v=>{v.project(camera);return {x:rect.left+(v.x+1)*rect.width/2,y:rect.top+(1-v.y)*rect.height/2}};
            const center=screen(tc.object.getWorldPosition(new THREE.Vector3())),points=[];
            for(const mesh of picker.children.filter(o=>o.name===${JSON.stringify(axis)}&&o.visible)){
              const p=mesh.geometry.attributes.position,idx=mesh.geometry.index,total=idx?idx.count:p.count;
              for(let i=0;i+2<total;i+=3){
                const v=new THREE.Vector3();for(let k=0;k<3;k++)v.add(new THREE.Vector3().fromBufferAttribute(p,idx?idx.getX(i+k):i+k));v.multiplyScalar(1/3).applyMatrix4(mesh.matrixWorld);
                const point=screen(v);ray.setFromCamera(new THREE.Vector2((point.x-rect.left)/rect.width*2-1,1-(point.y-rect.top)/rect.height*2),camera);
                const hit=ray.intersectObjects(picker.children,false).find(h=>h.object.visible);
                if(hit?.object.name===${JSON.stringify(axis)}&&point.x>rect.left+5&&point.x<rect.right-5&&point.y>rect.top+100&&point.y<rect.bottom-100)points.push(point);
              }
            }
            points.sort((a,b)=>Math.hypot(b.x-center.x,b.y-center.y)-Math.hypot(a.x-center.x,a.y-center.y));
            return {point:points[0],center,mode:tc.mode};
          })()`});
          if(projected.exceptionDetails)throw new Error(JSON.stringify(projected.exceptionDetails));
          const result=projected.result.value;if(!result.point)throw new Error('No pickable '+mode+' '+axis+' handle');
          return result;
        } finally {await send('Debugger.removeBreakpoint',{breakpointId:bp.breakpointId});await send('Debugger.resume');await click;}
      };
      const drag = async (h,end,cancel=false) => {
        const start=h.point;
        await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:start.x,y:start.y,buttons:0});
        await send('Input.dispatchMouseEvent',{type:'mousePressed',x:start.x,y:start.y,button:'left',buttons:1,clickCount:1});
        for(let k=1;k<=8;k++)await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:start.x+(end.x-start.x)*k/8,y:start.y+(end.y-start.y)*k/8,button:'left',buttons:1});
        if(cancel){await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape'});}
        await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:end.x,y:end.y,button:'left',buttons:0,clickCount:1});
      };
      const before=await hash();
      const move=await handle('translate','Y');await drag(move,{x:move.point.x,y:move.point.y-35});await stable(1);
      const moved=await hash();if(moved===before)throw new Error('Real pointer move did not persist');
      const rotate=await handle('rotate','Z'),dx=rotate.point.x-rotate.center.x,dy=rotate.point.y-rotate.center.y,a=0.35;
      await drag(rotate,{x:rotate.center.x+dx*Math.cos(a)-dy*Math.sin(a),y:rotate.center.y+dx*Math.sin(a)+dy*Math.cos(a)});await stable(2);
      const rotated=await hash();if(rotated===moved)throw new Error('Real pointer rotation did not persist');
      const scale=await handle('scale','X'),sx=scale.point.x-scale.center.x,sy=scale.point.y-scale.center.y,len=Math.hypot(sx,sy);
      await drag(scale,{x:scale.point.x+sx/len*25,y:scale.point.y+sy/len*25});await stable(3);
      const scaled=await hash();if(scaled===rotated)throw new Error('Real pointer scale did not persist');
      const cancel=await handle('translate','Y');await drag(cancel,{x:cancel.point.x,y:cancel.point.y-25},true);await delay(300);
      if(await hash()!==scaled)throw new Error('Escape did not cancel the preview');
      await evaluate(`document.querySelector('#btnUndo').click()`);await stable(2);
      if(await hash()!==rotated)throw new Error('Gizmo undo did not restore rotation');
      await evaluate(`document.querySelector('#btnRedo').click()`);await stable(3);
      if(await hash()!==scaled)throw new Error('Gizmo redo did not restore scale');
      await send('Debugger.disable');
      ui.viewportMoveRotateScale = true;ui.viewportEscapeCancel=true;ui.viewportUndoRedo=true;
    }
    if (process.env.FH1_SMOKE_CUSTOM === '1') {
      const bodyIndex = await evaluate(`fetch('/api/summary').then(r=>r.json()).then(s=>s.sections.find(s=>s.name==='body').index)`);
      const before = await evaluate(`fetch('/api/summary').then(r=>r.json()).then(s=>s.sections[${bodyIndex}].subs.reduce((n,s)=>n+s.tris,0))`);
      const obj = 'o Custom\nv 0 0 0\nv 0.2 0 0\nv 0 0.2 0\nf 1 2 3\n';
      await evaluate(`(()=>{const input=document.querySelector('#modelIn'); input.click=()=>{window.modelPickerOpened=true};document.querySelector('#btnCustomModel').click();const data=new DataTransfer();data.items.add(new File([${JSON.stringify(obj)}],'double-sided-test.obj',{type:'text/plain'}));input.files=data.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await evaluate(`!!document.querySelector('#mDoubleSided')`)) break;
        await delay(100);
      }
      const controls = await evaluate(`({picker:window.modelPickerOpened,both:document.querySelector('#mBothModels')?.checked,double:document.querySelector('#mDoubleSided')?.checked,apply:!!document.querySelector('#btnApplyCar'),zip:!!document.querySelector('#btnCarZip')})`);
      if (!controls.picker || !controls.both || controls.double !== false || !controls.apply || !controls.zip) throw new Error('Custom model controls failed: '+JSON.stringify(controls));
      const lodCount = await evaluate(`document.querySelectorAll('#tab-model input[data-lod]:checked').length`);
      await evaluate(`document.querySelector('#mTarget').value='${bodyIndex}';document.querySelector('#mDoubleSided').click();[...document.querySelectorAll('#tab-model button')].find(b=>b.textContent==='Add to car').click()`);
      let after;
      for (let attempt = 0; attempt < 160; attempt++) {
        after = await evaluate(`fetch('/api/summary').then(r=>r.json()).then(s=>s.sections[${bodyIndex}].subs.reduce((n,s)=>n+s.tris,0))`);
        if (after === before + 2 * lodCount) break;
        await delay(100);
      }
      if (after !== before + 2 * lodCount) throw new Error('Custom model did not add exported front and back triangles: '+JSON.stringify({before,after,lodCount}));
      ui.customModelDoubleSided = true; ui.customModelBothModelsDefault = true;
    }
    await evaluate(`document.querySelector('#optTex').value='auto';document.querySelector('#optTex').dispatchEvent(new Event('change'))`);
    await delay(2000);
    if (process.env.FH1_SMOKE_TEXTURES === '1') {
      ui.decodedTextures = {};
      for (const atlas of ['nodamage', 'damage', 'lights', 'interior']) {
        const texture = await evaluate(`(async()=>{const response=await fetch('/api/atlas?name='+${JSON.stringify(atlas)});if(!response.ok)throw new Error(await response.text());const image=await createImageBitmap(await response.blob());const size={width:image.width,height:image.height};image.close();return size;})()`);
        if (!(texture.width > 1 && texture.height > 1)) throw new Error('Texture failed to decode: '+atlas);
        ui.decodedTextures[atlas] = texture;
      }
      const visibleError = await evaluate(`document.querySelector('#errorBanner').hidden ? '' : document.querySelector('#errorText').value`);
      if (visibleError) throw new Error('Texture preview error: '+visibleError);
    }
    if (process.env.FH1_SMOKE_CLEAR_TEXTURES === '1') {
      const before = await evaluate(`performance.getEntriesByType('resource').filter(entry=>entry.name.includes('/api/atlas?')).length`);
      await evaluate(`document.querySelector('#btnDlc').click()`);
      for (let attempt = 0; attempt < 200; attempt++) {
        if (await evaluate(`[...document.querySelectorAll('#tab-file button')].some(button=>button.textContent==='Clear staged')`)) break;
        await delay(100);
      }
      await evaluate(`[...document.querySelectorAll('#tab-file button')].find(button=>button.textContent==='Clear staged').click()`);
      let after = before;
      for (let attempt = 0; attempt < 80; attempt++) {
        after = await evaluate(`performance.getEntriesByType('resource').filter(entry=>entry.name.includes('/api/atlas?')).length`);
        if (after > before) break;
        await delay(100);
      }
      if (after <= before) throw new Error('Clearing staged textures did not reload the displayed atlas');
      ui.clearStagedReloadedTextures = true;
    }
    if (process.env.FH1_SMOKE_PLACEMENT === '1') {
      const summary = await evaluate(`fetch('/api/summary').then(r=>r.json())`);
      const hood = summary.sections.find(s => s.name === 'hooda');
      const bumper = summary.sections.find(s => s.name === 'bumperFa');
      const wing = summary.sections.find(s => s.name === 'wingrace');
      const body = summary.sections.find(s => s.name === 'body');
      const context = async (index, action) => {
        await evaluate(`document.querySelector('#tree .sec[data-si="${index}"] .row').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:100,clientY:150}));[...document.querySelectorAll('#partContextMenu button')].find(b=>b.textContent===${JSON.stringify(action)}).click()`);
      };
      await context(hood.index, 'Copy coordinates');
      await context(bumper.index, 'Paste coordinates');
      let copied = false;
      for (let attempt = 0; attempt < 100; attempt++) {
        copied = await evaluate(`fetch('/api/summary').then(r=>r.json()).then(s=>s.sections[${bumper.index}].offset.every((v,i)=>Math.abs(v-${JSON.stringify(hood.offset)}[i])<0.00001))`);
        if (copied) break;
        await delay(100);
      }
      if (!copied) throw new Error('Coordinate paste did not align the part pivot');
      await evaluate(`document.querySelector('#btnUndo').click()`);
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await evaluate(`fetch('/api/summary').then(r=>r.json()).then(s=>s.undo===0)`)) break;
        await delay(100);
      }
      await context(wing.index, 'Merge into…');
      await evaluate(`document.querySelector('#mergeTarget').value=${JSON.stringify(String(body.index))};document.querySelector('#mergeBoth').checked=true;document.querySelector('#mergeDialog button').click()`);
      let merged = false;
      for (let attempt = 0; attempt < 150; attempt++) {
        merged = await evaluate(`fetch('/api/summary').then(r=>r.json()).then(s=>s.sections[${wing.index}].subs.length===0)`);
        if (merged) break;
        await delay(100);
      }
      if (!merged) throw new Error('Paired merge did not empty the source slot');
      const staged = await evaluate(`fetch('/api/clone/defaults').then(r=>r.json()).then(s=>s.staged)`);
      if (!staged.some(n=>/_lod0.carbin$/i.test(n)) || !staged.some(n=>n.toLowerCase()==='alf_8c_08.carbin')) throw new Error('Both files were not staged');
      ui.coordinatePaste = true; ui.pairedMerge = true;
    }
    if (process.env.FH1_SMOKE_PASTE !== '0') {
    await evaluate(`document.querySelector('#tree .sec .row').click();[...document.querySelectorAll('#tab-part button')].find(button=>button.textContent.startsWith('Copy part')).click()`);
    for (let attempt = 0; attempt < 80; attempt++) {
      if (await evaluate('!!document.querySelector("#pastePart")')) break;
      await delay(100);
    }
    await evaluate('document.querySelector("#pastePart").click()');
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await evaluate(`document.querySelectorAll('#tree .sec').length === ${parts + 1}`)) break;
      await delay(100);
    }
    const pastedParts = await evaluate('document.querySelectorAll("#tree .sec").length');
    if (pastedParts !== parts + 1) throw new Error('Packaged part paste failed');
    ui.pastedParts = pastedParts;
    }
    await delay(800);
    ui = { ...ui, cars, importedParts: parts, errors: await evaluate('window.editorErrors'), configuredPaths: true };
    if (ui.errors.length) throw new Error(JSON.stringify(ui));
  }
  if (process.env.FH1_SMOKE_UNLOAD === '1') {
    await evaluate(`document.querySelector('#btnUnload').click()`);
    let empty=false;
    for(let attempt=0;attempt<100;attempt++) {
      empty=await evaluate(`fetch('/api/status').then(r=>r.json()).then(s=>!s.open&&document.querySelectorAll('#tree .sec').length===0&&document.querySelector('#fileName').textContent==='no car open')`);
      if(empty)break;
      await delay(100);
    }
    if(!empty)throw new Error('Unload did not clear the active car');
    const errors=await evaluate('window.editorErrors||[]');
    if(errors.length)throw new Error('Unload UI error: '+JSON.stringify(errors));
    await evaluate(`document.querySelector('.car-row').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:1100,clientY:350}));document.querySelector('#carContextMenu button').click()`);
    for(let attempt=0;attempt<160;attempt++) {
      if(await evaluate(`document.querySelectorAll('#tree .sec').length>0`))break;
      await delay(100);
    }
    if(!await evaluate(`document.querySelectorAll('#tree .sec').length>0`))throw new Error('Car did not reopen after unload');
    ui.unloadAndReopen = true;
  }
  await send('Page.bringToFront');
  const image = await send('Page.captureScreenshot', { format: 'png', fromSurface: false });
  await writeFile(output + '.png', Buffer.from(image.data, 'base64'));
  await writeFile(output + '.json', JSON.stringify({ ...ui, processId: child.pid, passed: true }, null, 2));
  console.log(JSON.stringify({ ...ui, passed: true }));
} finally {
  socket?.close();
  if (process.env.FH1_SMOKE_KEEP_OPEN === '1') child.unref();
  else if (child.exitCode === null) {
    await new Promise(resolve => {
      const stop = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      stop.on('exit', resolve); stop.on('error', resolve);
    });
  }
}
