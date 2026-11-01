const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:18086');
  await page.locator('#open').click();assert.match(await page.locator('#error').innerText(),/не готова/);
  await page.locator('#create').click();assert.match(await page.locator('#state').innerText(),/в очереди/);
  await page.locator('#finish').click();assert.match(await page.locator('#state').innerText(),/разбирается/);
  await page.locator('#finish').click();await page.locator('#open').click();
  await page.locator('#duplicate').click();await page.locator('#note').click();
  assert.match(await page.locator('#queue').innerText(),/разборов: 1/);
  await page.locator('#reason').fill('Проверено');await page.locator('#accept').click();
  await page.locator('#message').evaluate(button=>button.click());
  await page.locator('#save').click();assert.match(await page.locator('#error').innerText(),/Новая версия/);
  assert.equal(await page.locator('#accept').isDisabled(),true);
  await page.locator('#message').click();assert.match(await page.locator('#queue').innerText(),/ожидает: 3/);
  await page.locator('#finish').click();await page.locator('#finish').click();await page.locator('#open').click();
  await page.locator('#reason').fill('Проверено');await page.locator('#accept').click();await page.locator('#cancel').click();
  assert.doesNotMatch(await page.locator('#history').innerText(),/Подтверждено/);
  await page.locator('#accept').click();await page.locator('#save').click();assert.match(await page.locator('#history').innerText(),/Подтверждено/);
  assert.deepEqual(errors,[]);
  const output=path.join(__dirname,'..','validation-output');fs.mkdirSync(output,{recursive:true});
  await page.screenshot({path:path.join(output,'screen-events.png'),fullPage:true});
  fs.writeFileSync(path.join(output,'ui-check.json'),JSON.stringify({date:'2026-10-25',result:'PASS',checks:['queued/running/ready','read does not start inference','duplicate and internal note do not start inference','latest pending version','stale confirmation race rejected','cancel creates no action','explicit confirmation','no JS errors'],scope:'local LAB02 simulation only'},null,2)+'\n');
  console.log('EVENT UI PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
