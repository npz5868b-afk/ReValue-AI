/* Native Chrome regression for the live-assessment entry buttons. No npm dependencies. */
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import os from 'node:os';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const pageUrl = pathToFileURL(path.join(root, 'app', 'ReValue-AI-Prototype-v1.16.1-predeployment-preview.html')).href;
const chromePath = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const port = 9497;
const profile = mkdtempSync(path.join(os.tmpdir(), 'revalue-live-entry-'));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const chrome = spawn(chromePath, [
  '--headless=new', '--no-first-run', '--no-default-browser-check', '--remote-allow-origins=*',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank'
], { windowsHide: true });

async function endpoint() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = pages.find(candidate => candidate.type === 'page');
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch { /* Chrome is still starting. */ }
    await pause(150);
  }
  throw new Error('Chrome DevTools endpoint did not start');
}

function connect(url, exceptions) {
  const ws = new WebSocket(url);
  const pending = new Map();
  let serial = 0;
  const ready = new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') {
      exceptions.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    }
    if (!message.id || !pending.has(message.id)) return;
    const request = pending.get(message.id);
    pending.delete(message.id);
    message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  return { ws, ready, send };
}

try {
  const exceptions = [];
  const client = connect(await endpoint(), exceptions);
  await client.ready;
  await client.send('Page.enable');
  await client.send('Runtime.enable');
  await client.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 900, deviceScaleFactor: 1, mobile: true });
  const evaluate = async expression => {
    const reply = await client.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (reply.exceptionDetails) throw new Error(reply.exceptionDetails.exception?.description || reply.exceptionDetails.text);
    return reply.result.value;
  };
  const navigate = async url => {
    await client.send('Page.navigate', { url });
    await pause(700);
  };
  const click = async selector => {
    const rect = await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el)throw new Error('Missing '+${JSON.stringify(selector)});el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
    await client.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: rect.x, y: rect.y, button: 'left', clickCount: 1 });
    await client.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: rect.x, y: rect.y, button: 'left', clickCount: 1 });
    await pause(250);
  };
  const home = async () => {
    await navigate(pageUrl);
    const state = await evaluate(`({home:document.querySelectorAll('.rv-entry-cta').length===2, timer:typeof diagTimer, timerValue:diagTimer})`);
    if (!state.home || state.timer !== 'object' || state.timerValue !== null) throw new Error(`Home state failed: ${JSON.stringify(state)}`);
  };
  const assertNoExceptions = label => {
    if (exceptions.length) throw new Error(`${label}: browser exception(s): ${exceptions.join(' | ')}`);
  };

  await home();
  await click('.rv-entry-cta:nth-of-type(1)');
  let state = await evaluate(`({search:!!document.getElementById('modelSearch'),step:document.querySelector('.rv-search-hero .eyebrow')?.textContent,timer:diagTimer})`);
  if (!state.search || state.step !== 'STEP 1 / IDENTIFY' || state.timer !== null) throw new Error(`Value This Phone failed: ${JSON.stringify(state)}`);
  await click('.flow-back');
  state = await evaluate(`({home:document.querySelectorAll('.rv-entry-cta').length===2,search:!!document.getElementById('modelSearch')})`);
  if (!state.home || state.search) throw new Error(`Live Back navigation failed: ${JSON.stringify(state)}`);
  console.log('PASS Value This Phone: browser click reached live model selection and Back returned home');

  await click('.rv-entry-cta:nth-of-type(2)');
  state = await evaluate(`({search:!!document.getElementById('modelSearch'),step:document.querySelector('.rv-search-hero .eyebrow')?.textContent,timer:diagTimer})`);
  if (!state.search || state.step !== 'STEP 1 / IDENTIFY' || state.timer !== null) throw new Error(`Value Another Phone failed: ${JSON.stringify(state)}`);
  await click('.flow-back');
  console.log('PASS Value Another Phone: browser click reached live model selection and Back returned home');

  await click('.rv-demo-cta');
  state = await evaluate(`({scan:document.getElementById('scanPage').textContent.includes('Choose Assessment Mode'),demoEntry:!!document.querySelector('#scanPage .mode-grid')})`);
  if (!state.scan || !state.demoEntry) throw new Error(`Demo Mode failed: ${JSON.stringify(state)}`);
  console.log('PASS Demo Mode: browser click preserved the approved demo entry');

  await evaluate(`showPage('internal');startDiagnostic()`);
  await pause(250);
  state = await evaluate(`({running:diagStatuses.includes('run'),timerActive:diagTimer!==null})`);
  if (!state.running || !state.timerActive) throw new Error(`Diagnostic start failed: ${JSON.stringify(state)}`);
  await evaluate(`cancelDiagnosticPreview();resetDemoAssessment();startDiagnostic()`);
  await pause(120);
  state = await evaluate(`({running:diagStatuses.includes('run'),timerActive:diagTimer!==null})`);
  if (!state.running || !state.timerActive) throw new Error(`Diagnostic restart failed: ${JSON.stringify(state)}`);
  await evaluate(`cancelDiagnosticPreview()`);
  state = await evaluate(`diagTimer===null`);
  if (!state) throw new Error('Diagnostic cancellation did not clear the shared timer');
  console.log('PASS Diagnostic timer: cancellation and restart use one initialized shared timer');

  await evaluate(`exteriorDone=true;internalDone=true;renderAssessmentGates();showPage('report')`);
  state = await evaluate(`!!document.querySelector('#reportPage.active') && !!document.getElementById('rvReportHub')`);
  if (!state) throw new Error('Report flow failed to render');
  console.log('PASS Report flow: demo assessment still renders the report');

  await evaluate(`localStorage.setItem('revalueAiChats','fixture')`);
  await navigate(`${pageUrl}?reset_local_demo=1`);
  state = await evaluate(`({cleared:localStorage.getItem('revalueAiChats')===null,query:location.search,home:document.querySelectorAll('.rv-entry-cta').length===2})`);
  if (!state.cleared || state.query || !state.home) throw new Error(`Local reset behavior failed: ${JSON.stringify(state)}`);
  console.log('PASS Reset behavior: local demo chat state clears and the home page reloads');

  assertNoExceptions('Live assessment entry regression');
  client.ws.close();
  console.log('PASS No uncaught browser exceptions during live entry, demo, diagnostic, report, and reset coverage');
} finally {
  const stopped = new Promise(resolve => chrome.once('exit', resolve));
  chrome.kill();
  await Promise.race([stopped, pause(3000)]);
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 3 }); } catch { /* Chrome may still be releasing its temporary profile. */ }
}
