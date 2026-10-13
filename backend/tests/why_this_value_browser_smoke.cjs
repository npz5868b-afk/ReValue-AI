// Uses the already available Playwright/Chromium; adds no app dependencies.
// Start the normal backend first, then: node backend/tests/why_this_value_browser_smoke.cjs
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');
const base = process.env.REVALUE_TEST_BASE || 'http://127.0.0.1:8766';
const root = path.resolve(__dirname, '../..');
(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless:true, args:['--no-sandbox']});
  const context = await browser.newContext({viewport:{width:390,height:844}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  let mode = 'seed', response;
  // Isolate this report feature from inference; exercise the existing five-upload UI.
  await page.route('**/analyze-exterior', route => route.fulfill({json:{status:'ok',grade:{exterior_grade:mode==='calibration'?'C-':'A'},merged_damage:[],detections:[]}}));
  await page.route('**/live-valuation', async route => {
    response = await (await route.fetch()).json();
    if(mode==='older')delete response.explainability;
    if(mode==='xss'){
      response.explainability.factors[0].value = '<img src=x onerror="window.explanationXss=true">';
      response.explainability.method.summary = '<script>window.explanationXss=true</script>';
      response.explainability.caveats.push('<svg onload="window.explanationXss=true">');
    }
    await route.fulfill({json:response});
  });
  async function report(brand,model,storage='256GB') {
    await page.goto(base);
    await page.getByRole('button',{name:'Value Another Phone'}).click();
    await page.locator('#modelSearch').fill(`${brand} ${model}`);
    await page.locator('.catalogue-result').filter({hasText:`${brand} ${model}`}).first().click();
    await page.locator('.storage-grid').waitFor({state:'visible'});
    await page.evaluate(async storage => {setFinalStorage(storage);await confirmFinalStorage()}, storage);
    let count = 0;
    for(const view of ['front','back','camera','left_side','right_side']){
      await page.locator(`#finalInput-${view}`).setInputFiles(path.join(root,'app/iphone13pro_back_damage.jpg'));
      await page.waitForFunction(n => document.querySelectorAll('.final-slot.ready').length===n, ++count);
    }
    await page.evaluate(() => analyzeFinalExterior());
    // Diagnostic completion is a fixture; diagnostics are outside this feature's scope.
    await page.evaluate(() => {internalDone=true;showPage('report')});
    const before = await page.locator('#estimatedValue').innerText();
    const toggle = page.locator('#priceEvidenceToggle');
    assert.equal(await toggle.innerText(),'Why this value?');
    assert.equal(await toggle.getAttribute('aria-expanded'),'false');
    await toggle.focus();
    await page.keyboard.press('Enter');
    assert.equal(await toggle.getAttribute('aria-expanded'),'true');
    assert.equal(await page.locator('#estimatedValue').innerText(),before);
    assert.equal(await page.locator('#priceEvidencePanel').isVisible(),true);
    assert.equal(await page.locator('#priceEvidencePanel').count(),1);
    return {before,text:await page.locator('#priceEvidencePanel').innerText()};
  }
  let result = await report('Samsung','Galaxy S24 Ultra');
  assert.equal(result.before,`RM${response.value_low.toLocaleString('en-MY')}–RM${response.value_high.toLocaleString('en-MY')}`);
  assert.match(result.text,/Local Malaysian public evidence seed/);
  assert.match(result.text,/11 exact-model\/storage comparables · 7 condition-matched/);
  assert.match(result.text,/Strong local evidence/);
  assert.match(result.text,/not refreshed/);
  assert.equal(await page.evaluate(() => currentAssessment.valuation.explainability.version),'why_this_value_v1');
  const normalDocumentWidth=await page.evaluate(() => document.documentElement.scrollWidth);
  await page.screenshot({path:'/tmp/revalue-why-this-value-mobile.png',fullPage:true});
  await page.locator('#priceEvidenceToggle').click();
  assert.equal(await page.locator('#priceEvidenceToggle').getAttribute('aria-expanded'),'false');
  assert.equal(await page.locator('#priceEvidencePanel').isVisible(),false);
  await page.evaluate(() => {showPage('shops');openChat(0,true,false)});
  assert.ok((await page.locator('#chatThread').innerText()).includes('Samsung Galaxy S24 Ultra'));
  assert.equal(await page.evaluate(() => currentAssessment.valuation.explainability.version),'why_this_value_v1');
  console.log('Seed report, keyboard toggle, payload preservation, shop/report/chat flow: passed');

  mode='calibration';result=await report('Apple','iPhone 13 Pro');
  assert.match(result.text,/Local Malaysian calibration evidence/);
  assert.match(result.text,/ADDITIONAL CONTEXT/);
  assert.match(result.text,/not included in the resale market range/);
  assert.ok(result.text.includes(response.distribution.explanation));
  console.log('Calibrated fallback, original explanation, separate benchmarks: passed');

  mode='insufficient';result=await report('Samsung','Galaxy S24','512GB');
  assert.equal(result.before,'Insufficient market evidence');
  assert.match(result.text,/insufficient reliable/);
  assert.equal(response.value_low,null);
  console.log('Unsupported fallback / insufficient evidence: passed');

  mode='older';result=await report('Samsung','Galaxy S24 Ultra');
  assert.match(result.text,/Local Malaysian fallback evidence/);
  assert.equal(await page.evaluate(() => currentAssessment.valuation.explainability),undefined);
  console.log('Older response without explainability: passed');

  mode='xss';result=await report('Samsung','Galaxy S24 Ultra');
  assert.match(result.text,/<img src=x/);
  assert.equal(await page.locator('#priceEvidencePanel img, #priceEvidencePanel script, #priceEvidencePanel svg').count(),0);
  assert.equal(await page.evaluate(() => window.explanationXss),undefined);
  const layout=await page.evaluate(() => ({width:document.documentElement.scrollWidth,panelRight:document.getElementById('priceEvidencePanel').getBoundingClientRect().right,viewport:innerWidth}));
  assert.ok(layout.width<=normalDocumentWidth,'long evidence must not increase page width');
  assert.ok(layout.panelRight<=layout.viewport,'evidence panel must fit mobile viewport');
  console.log('Backend text escaped; mobile panel fits without additional page overflow: passed');

  await page.evaluate(() => {startDemoMode();exteriorDone=true;internalDone=true;showPage('report')});
  if(await page.locator('#priceEvidenceToggle').getAttribute('aria-expanded')!=='true')await page.locator('#priceEvidenceToggle').click();
  const demo = await page.locator('#priceEvidencePanel').innerText();
  assert.match(demo,/Trade-in benchmark/);
  assert.match(demo,/Repair evidence/i);
  assert.match(demo,/Valuation Method/i);
  assert.match(await page.locator('#estimatedValue').innerText(),/RM1,150–RM1,450/);
  assert.deepEqual(errors,[]);
  console.log('Rich Demo evidence and demo range preserved; zero browser errors: passed');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
