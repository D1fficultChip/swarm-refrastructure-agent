// Render original online trial records through the real replay API, without a new model call.
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import assert from 'node:assert/strict'
import {chromium} from '@playwright/test'

const root = path.resolve(import.meta.dirname, '../..')
const artifacts = path.join(root, 'artifacts/phase6')
const sources = [['agent', 'SC08', 'sc08-adaptive'], ['feedback', 'SC07', 'sc07-feedback'], ['bounded', 'SC08', 'sc08-bounded']]
const output = path.join(root, 'artifacts/phase61/replays')
fs.mkdirSync(output, {recursive: true})
const metadata = []
const browser = await chromium.launch({executablePath: process.env.DEMO_CHROME_PATH || undefined})
try {
  for (const [group, scenario, name] of sources) {
    const rows = fs.readFileSync(path.join(artifacts, group, 'trials.jsonl'), 'utf8').trim().split('\n').map(JSON.parse)
    const row = rows.find(row => row.scenario_id === scenario)
    assert.ok(row)
    const source = path.join(artifacts, group, row.raw_file)
    const raw = fs.readFileSync(source)
    const run = JSON.parse(raw)
    fs.mkdirSync(path.join(artifacts, 'demo/runs'), {recursive: true})
    fs.copyFileSync(source, path.join(artifacts, 'demo/runs', run.run_id + '.json'))
    const page = await browser.newPage({viewport: {width: 1600, height: 1100}})
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('http://127.0.0.1:5173')
    await page.getByLabel('历史运行').selectOption(run.run_id)
    await page.getByRole('button', {name: '展示完整过程'}).click()
    await page.getByTestId('validation-status').filter({hasText: '已通过当前已建模约束校核'}).waitFor()
    if (scenario === 'SC07') {
      await page.getByRole('button', {name: '查看首次校核失败'}).click()
    }
    assert.equal(await page.getByRole('button', {name: '3 运行重构'}).isDisabled(), true)
    const api = await page.request.get('http://127.0.0.1:5173/api/v1/demo/run/' + run.run_id)
    assert.deepEqual(await api.json(), run)
    await page.screenshot({path: path.join(output, name + '.png'), fullPage: true})
    assert.deepEqual(errors, [])
    metadata.push({screenshot: name + '.png', run_id: run.run_id, scenario, mode: run.execution_mode,
      original_file: path.relative(root, source), sha256: crypto.createHash('sha256').update(raw).digest('hex'),
      data_origin: 'Unmodified real Qwen online trial, rendered via persisted replay API', captured_at: new Date().toISOString(), page_errors: errors})
    await page.close()
  }
} finally {
  await browser.close()
}
fs.writeFileSync(path.join(output, 'online-metadata.json'), JSON.stringify(metadata, null, 2) + '\n')
console.log('Captured 3 real online trial replays; no additional model calls.')
