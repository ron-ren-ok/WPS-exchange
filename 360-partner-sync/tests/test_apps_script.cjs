const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const ctx = vm.createContext({console});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../apps-script/Code.gs'), 'utf8'), ctx);
const headers = ['日期', '合作方', '运营位', '新增', '血量'];
const sourceHeaders = ['日期', '360-1', '360-2', '360-3', '360-4', '360-5'];
const day = ctx.day360_('2026-10-14');
const plan = (source, target = [headers], start = day, end = day) =>
  JSON.parse(JSON.stringify(ctx.plan360_(source, target, start, end)));

test('blank source and 360-4 zero skipped; other zero kept; blood never populated', () => {
  const result = plan([sourceHeaders, ['汇总'], ['2026-10-14', 0, '', 3, 0, '1,234']]);
  assert.equal(result.appends.length, 3);
  assert.equal(result.unavailable.length, 2);
  assert.equal(result.appends.find(r => r[2] === '换量弹窗')[3], 0);
  assert.equal(result.appends.find(r => r[2] === '文档雷达')[3], 1234);
  assert.ok(result.appends.every(r => r[4] === ''));
});
test('only blank 新增 updated; existing conflicts and 血量 preserved; rerun idempotent', () => {
  const source = [sourceHeaders, ['2026-10-14', 10, 20, 30, 40, 50]];
  const target = [headers, ['2026-10-14', '360', '气泡', '', 999],
    ['2026-10-14', '360', '换量弹窗', 8, '=SUM(1,2)']];
  const result = plan(source, target);
  assert.deepEqual(result.updates, [{range: "'合作方新增血量'!D2", values: [[20]]}]);
  target[1][3] = 20;
  const rerun = plan(source, target.concat(result.appends));
  assert.equal(rerun.updates.length + rerun.appends.length, 0);
  assert.equal(target[2][4], '=SUM(1,2)');
});
test('daily mode repairs rolling window and catches up latest date; history explicit', () => {
  const source = [sourceHeaders, ['2026-01-01', 1, 1, 1, 1, 1],
    ['2026-09-01', 2, 2, 2, 2, 2], ['2026-10-14', 3, 3, 3, 3, 3]];
  const target = [headers, ['2026-08-31', '360', '气泡', 5, '']];
  const daily = plan(source, target, null);
  assert.ok(!daily.appends.some(r => r[0] === '2026-01-01'));
  assert.equal(daily.appends.filter(r => r[0] === '2026-09-01').length, 1);
  assert.equal(plan(source, target, ctx.day360_('2026-01-01')).appends.filter(r => r[0] === '2026-01-01').length, 5);
});
test('source/target duplicates and invalid metrics stop planning', () => {
  const row = ['2026-10-14', 1, 2, 3, 4, 5];
  assert.throws(() => plan([sourceHeaders, row, row]), /源表重复/);
  const record = ['2026-10-14', '360', '气泡', '', 1];
  assert.throws(() => plan([sourceHeaders, row], [headers, record, record]), /目标表重复/);
  assert.throws(() => plan([sourceHeaders, ['2026-10-14', 'oops']]), /无效新增/);
});
test('serial dates, leap days and reordered target headers', () => {
  assert.equal(ctx.day360_(day + 25569), day);
  assert.throws(() => ctx.day360_('2026-02-29'), /无效日期/);
  const target = [['血量', '新增', '运营位', '合作方', '日期'], [999, '', '气泡', '360', '2026-10-14']];
  assert.equal(plan([sourceHeaders, ['2026-10-14', '', 2]], target).updates[0].range, "'合作方新增血量'!B2");
});
