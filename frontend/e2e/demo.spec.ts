import {test,expect,Page} from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const dir=path.resolve('../artifacts/phase61/browser')
fs.mkdirSync(dir,{recursive:true})
const dynamicDir=path.resolve('../artifacts/phase62/browser')
fs.mkdirSync(dynamicDir,{recursive:true})
async function screenshot(page:Page,name:string){await page.screenshot({path:path.join(dir,name+'.png'),fullPage:true});fs.writeFileSync(path.join(dir,name+'.txt'),await page.locator('body').innerText())}
async function load(page:Page,id:string){await page.goto('/');await page.locator('#scenario').selectOption(id);await page.getByRole('button',{name:'加载场景',exact:true}).click();await expect(page.getByTestId('scenario-brief')).toBeVisible()}
async function inject(page:Page){await page.getByRole('button',{name:'1 注入事件'}).click();await expect(page.getByTestId('plan-status')).toContainText('需要重构')}
async function reconstruct(page:Page){await page.getByRole('button',{name:'3 运行重构'}).click();await expect(page.getByRole('button',{name:'4 校核并提交'})).toBeEnabled({timeout:180000})}
async function result(page:Page,name:string){const response=await page.request.get('http://127.0.0.1:5173'+await page.locator('a[href^="/api/v1/demo/run/"]').getAttribute('href'));const r=await response.json();fs.writeFileSync(path.join(dir,name+'-run.json'),JSON.stringify(r,null,2));return r}

test('SC08 Chinese six-question flow with real state, preview and replay',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 await page.goto('/');await expect(page.getByRole('heading',{name:'SC08 · 综合态势突变'})).toBeVisible();await screenshot(page,'home')
 await load(page,'SC08');await screenshot(page,'SC08-initial')
 await expect(page.getByTestId('scenario-brief')).toContainText('60 个节点');await expect(page.getByTestId('scenario-brief')).toContainText('6 个编队');await expect(page.getByTestId('scenario-brief')).toContainText('8 项任务')
 await inject(page);await screenshot(page,'SC08-event')
 await expect(page.getByTestId('scenario-brief')).toContainText('5/8')
 for(const task of ['T03','T04','T05'])await expect(page.getByTestId('cause-'+task)).toBeVisible()
 await expect(page.getByTestId('cause-T03')).toContainText('中继能力不足：需求 1 / 当前可用 0 / 缺口 1')
 await expect(page.getByTestId('cause-T05')).toContainText('航迹与限制区域冲突')
 await expect(page.locator('details[open]')).toHaveCount(0)
 await page.getByRole('button',{name:'初始方案',exact:true}).click();await expect(page.getByTestId('map-caption')).toContainText('初始')
 await page.getByRole('button',{name:'当前方案',exact:true}).click()
 await page.getByRole('button',{name:'3 运行重构'}).click();await screenshot(page,'SC08-running');await expect(page.getByRole('button',{name:'4 校核并提交'})).toBeEnabled({timeout:30000})
 await screenshot(page,'SC08-preview');await expect(page.getByTestId('feasible-after')).toHaveText('5/8');await expect(page.getByTestId('changes')).toContainText('待提交方案变更')
 await page.getByRole('button',{name:'4 校核并提交'}).click();await expect(page.getByTestId('feasible-after')).toHaveText('8/8')
 await expect(page.getByTestId('preservation')).toContainText('5 项未受影响任务保持原方案');await expect(page.getByTestId('preservation')).toContainText('7 条航迹保持不变')
 await expect(page.getByTestId('outcome')).toContainText('8.54%');await expect(page.getByTestId('validation-status')).toContainText('已通过当前已建模约束校核')
 await page.getByRole('button',{name:'重构后方案',exact:true}).click();await screenshot(page,'SC08-result')
 await page.getByRole('button',{name:'叠加对比',exact:true}).click();await screenshot(page,'SC08-overlay')
 await page.getByRole('button',{name:'态势变化后 / 重构前',exact:true}).click();await screenshot(page,'SC08-before')
 await page.getByRole('button',{name:'叠加对比',exact:true}).click()
 const r=await result(page,'SC08');expect(r.before_after_metrics.after.nodes.FAILED).toBe(2);expect(r.policy_metrics.model_calls).toBe(0);expect(r.commit.committed_version).toBe(5)
 await page.getByLabel('历史运行').selectOption(r.run_id);await expect(page.getByRole('button',{name:'3 运行重构'})).toBeDisabled();await page.getByRole('button',{name:'展示完整过程'}).click();await screenshot(page,'SC08-replay')
 expect(errors).toEqual([]);fs.writeFileSync(path.join(dir,'SC08-metadata.json'),JSON.stringify({run_id:r.run_id,origin:'Live backend, original solver, no mocked response',errors,captured_at:new Date().toISOString()},null,2))
})

test('SC03 preserves routes and exposes real candidate stages',async({page})=>{
 await load(page,'SC03');await inject(page);await reconstruct(page);await page.getByRole('button',{name:'4 校核并提交'}).click();await expect(page.getByTestId('feasible-after')).toHaveText('8/8')
 await expect(page.getByTestId('preservation')).toContainText('8 条航迹保持不变')
 await expect(page.getByTestId('candidate-funnel')).toContainText('候选资源筛选');await expect(page.locator('.funnel')).toContainText('状态可用');await screenshot(page,'SC03-candidates');await result(page,'SC03')
})

test('SC07 real model FAIL → feedback → scope expansion → PASS without raw JSON',async({page})=>{
 test.skip(!process.env.DEMO_REAL_AGENT,'Explicit opt-in for the authorized online model smoke test')
 test.setTimeout(240000)
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 await load(page,'SC07');await page.getByRole('button',{name:'智能体自适应模式',exact:true}).click();await inject(page);await page.getByRole('button',{name:'3 运行重构'}).click();await screenshot(page,'SC07-running')
 await expect(page.getByRole('button',{name:'4 校核并提交'})).toBeEnabled({timeout:180000})
 await expect(page.getByTestId('feedback-story')).toContainText('任务 T03的已检查约束满足');await expect(page.getByTestId('feedback-story')).toContainText('任务 T06被连带破坏');await expect(page.getByTestId('feedback-story')).toContainText('成员不满足任务可用时间窗口');await expect(page.getByTestId('feedback-story')).toContainText('扩大重构范围：加入任务 T06');await expect(page.getByTestId('feedback-story')).toContainText('选择替代方案')
 await expect(page.locator('details[open]')).toHaveCount(0)
 await page.getByTestId('feedback-story').scrollIntoViewIfNeeded();await screenshot(page,'SC07-validator-fail')
 await page.getByRole('button',{name:'查看后续校核结果'}).click();await expect(page.getByTestId('feedback-inspection')).toContainText('已通过当前已建模约束校核');await screenshot(page,'SC07-second-pass')
 await page.getByRole('button',{name:'4 校核并提交'}).click();await expect(page.getByTestId('feasible-after')).toHaveText('2/2');const r=await result(page,'SC07');expect(r.policy_metrics.pure_agent_success).toBe(true)
 await page.getByLabel('历史运行').selectOption(r.run_id);await page.getByRole('button',{name:'展示完整过程'}).click();await expect(page.getByTestId('feedback-story')).toContainText('任务 T06被连带破坏');await screenshot(page,'SC07-replay');expect(errors).toEqual([])
})

test('one-click final scenario and narrow screen remain usable',async({page})=>{
 await page.setViewportSize({width:760,height:1000});await page.goto('/');await page.getByRole('button',{name:'一键综合演示'}).click();await expect(page.getByTestId('feasible-after')).toHaveText('8/8',{timeout:30000});await expect(page.getByTestId('current-mode')).toContainText('实时确定性模式');expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);await screenshot(page,'SC08-narrow')
})

test('dynamic mission moves, handles no-op and two reconstruction cycles, then replays checkpoints',async({page})=>{
 test.setTimeout(90000)
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 await page.goto('/');await expect(page.locator('.experience-switch')).toContainText('固定场景 · 固定事件 · 单轮重构');await expect(page.locator('.experience-switch')).toContainText('节点持续运动')
 await page.getByRole('button',{name:'动态任务演示',exact:true}).click();await expect(page.getByLabel('动态演示操作顺序')).toBeVisible()
 await page.getByLabel('场景种子').fill('58372');await page.getByLabel('事件种子').fill('91420')
 await page.getByLabel('事件模式').selectOption('manual');await page.getByLabel('重构模式').selectOption('deterministic_realtime')
 await page.getByRole('button',{name:'生成新任务场景'}).click()
 await expect(page.getByRole('heading',{name:'随机挑战运行中'})).toBeVisible()
 await expect(page.getByLabel('当前操作步骤')).toContainText('开始运行')
 await expect(page.locator('.dynamic-facts')).toContainText('60');await expect(page.locator('.dynamic-facts')).toContainText('8')
 await page.getByRole('button',{name:'开始运行'}).click();await page.waitForTimeout(700);await page.getByRole('button',{name:'暂停'}).click()
 await page.getByRole('button',{name:'单步 +1s'}).click();await expect(page.locator('.dynamic-clock strong')).not.toHaveText('T+0.0s')

 await page.getByLabel('动态事件难度').selectOption('L1');await page.getByRole('button',{name:'生成动态事件'}).click()
 await expect(page.locator('.timeline')).toContainText('当前任务方案仍可继续')
 for(let cycle=0;cycle<2;cycle++){
  await page.getByLabel('动态事件难度').selectOption('L2');await page.getByRole('button',{name:'生成动态事件'}).click()
  await expect(page.locator('.timeline')).toContainText('检测到任务约束失效')
  await page.getByRole('button',{name:'重构并校核提交'}).click()
  await expect.poll(()=>page.getByText('重构方案通过校核并提交',{exact:true}).count(),{timeout:30000}).toBe(cycle+1)
 }
 await expect(page.locator('.dynamic-facts')).toContainText('8/8')
 await expect(page.getByRole('heading',{name:'本轮重构方案与算法执行'})).toBeVisible()
 await expect(page.getByTestId('changes')).toContainText('重构方案变更')
 await expect(page.getByRole('heading',{name:'重构调度与算法执行'})).toBeVisible()
 await expect(page.getByTestId('candidate-funnel')).toContainText('候选资源筛选')
 await expect(page.getByTestId('validation-status')).toContainText('已通过当前已建模约束校核')
 await expect(page.getByLabel('动态重构轮次').locator('option')).toHaveCount(3)
 await expect(page.locator('.dynamic-metrics>div').filter({hasText:'动态事件'}).locator('b')).toHaveText('3')
 await expect(page.locator('.dynamic-metrics>div').filter({hasText:'成功重构'}).locator('b')).toHaveText('2')
 await expect.poll(()=>page.getByLabel('动态历史运行').locator('option').count()).toBeGreaterThan(1)
 await page.getByLabel('动态历史运行').selectOption({index:1});await expect(page.getByLabel('动态回放时间点')).toBeVisible()
 await page.screenshot({path:path.join(dynamicDir,'dynamic-continuous-mission.png'),fullPage:true})
 fs.writeFileSync(path.join(dynamicDir,'dynamic-continuous-mission.txt'),await page.locator('body').innerText())
 expect(errors).toEqual([])
})
