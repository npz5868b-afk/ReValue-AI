const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.resolve(__dirname, '../../app/ReValue-AI-Prototype-v1.16.1-predeployment-preview.html'), 'utf8');

function functionLine(name) {
  const line = html.split(/\r?\n/).find(value => value.includes(name));
  assert.ok(line, `missing ${name}`);
  return line;
}

const confirmStorage = functionLine('window.confirmFinalStorage=async function');
assert.match(confirmStorage, /const generation=finalState\.assessmentGeneration/);
assert.match(confirmStorage, /!isCurrentAssessment\(generation,'storage'\)/);

const exterior = functionLine('window.analyzeFinalExterior=async function');
assert.match(exterior, /const analysis=await api\('\/analyze-exterior'/);
assert.match(exterior, /!isCurrentAssessment\(generation,'exterior'\)/);
assert.match(exterior, /valuation=await api\('\/live-valuation'/);
assert.match(exterior, /finalState\.analysis=analysis;finalState\.valuation=valuation/);

const report = functionLine('window.updateReport=function');
assert.match(report, /if\(!finalState\.active\)\{legacy\.updateReport\(\);return\}/);
assert.match(report, /if\(finalState\.decisionSupport\)renderDecisionSupport\(finalState\.decisionSupport\)/);

const refresh = html.slice(html.indexOf('function refreshDecisionSupport'), html.indexOf('function liveValueExplanationHtml'));
assert.match(refresh, /if\(key===decisionRequestKey\)\{if\(finalState\.decisionSupport\)renderDecisionSupport\(finalState\.decisionSupport\);return\}/);
assert.match(refresh, /decisionRequestKey='';/);

assert.match(html, /qrExplain:'Report reference'/);
assert.match(html, /qrExplain:'报告参考编号'/);
assert.doesNotMatch(html, /qrExplain:'Verified report ID'/);

console.log('integration hardening frontend regression tests passed: 6');
