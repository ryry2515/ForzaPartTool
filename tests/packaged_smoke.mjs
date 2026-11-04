import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';

const executable = process.argv[2];
const output = process.argv[3];
if (!executable || !output) throw new Error('Usage: node packaged_smoke.mjs EXE OUTPUT_PREFIX');
const listener = createServer();
await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const child = spawn(executable, [], { windowsHide: true, stdio: 'ignore', env: {
  ...process.env,
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-address=127.0.0.1 --remote-debugging-port=${port}`
} });
let socket;
const pending = new Map();
let serial = 0;
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
    setTimeout(() => { if (pending.delete(id)) reject(new Error('CDP timeout: ' + method)); }, 15000).unref();
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
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
  if (process.env.FH1_SMOKE_GAME) {
    await evaluate(`document.querySelector('#btnSettings').click();document.querySelector('#cfgGame').value=${JSON.stringify(process.env.FH1_SMOKE_GAME)};document.querySelector('#cfgDlc').value=${JSON.stringify(process.env.FH1_SMOKE_DLC || '')};document.querySelector('#applySettings').click()`);
    for (let attempt = 0; attempt < 480; attempt++) {
      if (await evaluate('document.querySelectorAll(".car-row").length > 176')) break;
      await delay(250);
    }
    const cars = await evaluate('document.querySelectorAll(".car-row").length');
    if (cars <= 176) throw new Error('Combined game/DLC indexing failed: ' + cars);
    await evaluate(`document.querySelector('#carSearch').value='ALF_8C';document.querySelector('#carSearch').dispatchEvent(new Event('input'));document.querySelector('.car-row').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:1100,clientY:350}));document.querySelector('#carContextMenu button').click()`);
    for (let attempt = 0; attempt < 160; attempt++) {
      if (await evaluate('document.querySelectorAll("#tree .sec").length > 0 && /parts$/.test(document.querySelector("#status").textContent)')) break;
      await delay(250);
    }
    const parts = await evaluate('document.querySelectorAll("#tree .sec").length');
    if (!parts) throw new Error('Packaged car import failed');
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
    await delay(800);
    ui = { ...ui, cars, importedParts: parts, pastedParts, errors: await evaluate('window.editorErrors'), configuredPaths: true };
    if (ui.errors.length) throw new Error(JSON.stringify(ui));
  }
  const image = await send('Page.captureScreenshot', { format: 'png' });
  await writeFile(output + '.png', Buffer.from(image.data, 'base64'));
  await writeFile(output + '.json', JSON.stringify({ ...ui, passed: true }, null, 2));
  console.log(JSON.stringify({ ...ui, passed: true }));
} finally {
  socket?.close();
  if (child.exitCode === null) {
    await new Promise(resolve => {
      const stop = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      stop.on('exit', resolve); stop.on('error', resolve);
    });
  }
}
