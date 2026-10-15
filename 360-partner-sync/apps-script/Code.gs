/** GitHub owns this source; Apps Script runs it using the trigger creator's account. */
const SYNC360 = Object.freeze({
  sourceId: '1fHVgG5EnrSR-BXOsQxmNbIM_fk88qwTHe8u-gkxsFvw',
  sourceTab: '每日',
  targetId: '1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4',
  targetTab: '合作方新增血量',
  timezone: 'Asia/Shanghai',
  surfaces: ['换量弹窗', '气泡', '卸载后引导H5', '卸载引导', '文档雷达']
});

function sync360Daily() { return run360_({}); }
function preview360Daily() { return run360_({dryRun: true}); }

/** Call from a temporary wrapper with explicit ISO dates for historical backfill. */
function sync360Range(startDate, endDate, dryRun) {
  if (!startDate) throw new Error('历史补数需要 startDate');
  return run360_({startDate: startDate, endDate: endDate, dryRun: dryRun === true});
}

/** Run once, after validating sync360Daily and disabling the GitHub schedule. */
function install360DailyTrigger() {
  const triggers = ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'sync360Daily');
  // Create before deleting, so a failed creation retains the old schedule.
  ScriptApp.newTrigger('sync360Daily').timeBased().atHour(3)
    .everyDays(1).inTimezone(SYNC360.timezone).create();
  triggers.forEach(t => ScriptApp.deleteTrigger(t));
}

function run360_(options) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) throw new Error('360 同步正在运行，请稍后重试');
  try {
    const today = day360_(Utilities.formatDate(new Date(), SYNC360.timezone, 'yyyy-MM-dd'));
    const end = options.endDate ? day360_(options.endDate) : today - 1;
    const start = options.startDate ? day360_(options.startDate) : null;
    if (start !== null && start > end) throw new Error('开始日期晚于结束日期');
    // Read serial dates, matching the Python sync. No dependence on source row continuity.
    const source = Sheets.Spreadsheets.Values.get(SYNC360.sourceId,
      "'每日'!A:F", {valueRenderOption: 'UNFORMATTED_VALUE', dateTimeRenderOption: 'SERIAL_NUMBER'}).values || [];
    const target = Sheets.Spreadsheets.Values.get(SYNC360.targetId,
      "'合作方新增血量'!A:E", {valueRenderOption: 'UNFORMATTED_VALUE', dateTimeRenderOption: 'SERIAL_NUMBER'}).values || [];
    const plan = plan360_(source, target, start, end);
    if (!options.dryRun) {
      // Only write missing 新增 cells; existing 血量 cells/formulas are untouched.
      for (let i = 0; i < plan.updates.length; i += 500) {
        Sheets.Spreadsheets.Values.batchUpdate({valueInputOption: 'RAW',
          data: plan.updates.slice(i, i + 500)}, SYNC360.targetId);
      }
      if (plan.appends.length) {
        Sheets.Spreadsheets.Values.append({values: plan.appends}, SYNC360.targetId,
          "'合作方新增血量'!A:E", {valueInputOption: 'USER_ENTERED', insertDataOption: 'INSERT_ROWS'});
      }
    }
    const result = {dry_run: !!options.dryRun, end: iso360_(end),
      updated_cells: plan.updates.length, appended_rows: plan.appends.length,
      unavailable_records: plan.unavailable};
    console.log(JSON.stringify(result));
    return result;
  } finally { lock.releaseLock(); }
}

function day360_(value) {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 20000 && value <= 80000)
    return Math.floor(value) - 25569;
  const match = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/.exec(String(value).trim().split(',')[0]);
  if (!match) throw new Error('无法解析日期: ' + value);
  const date = new Date(Date.UTC(+match[1], +match[2] - 1, +match[3]));
  if (date.getUTCFullYear() !== +match[1] || date.getUTCMonth() !== +match[2] - 1 || date.getUTCDate() !== +match[3])
    throw new Error('无效日期: ' + value);
  return date.getTime() / 86400000;
}
function iso360_(day) { return new Date(day * 86400000).toISOString().slice(0, 10); }
function blank360_(value) { return value === '' || value === null || value === undefined; }
function key360_(day, partner, surface) { return JSON.stringify([day, partner, surface]); }
function column360_(index) { return String.fromCharCode(65 + index); }

/** Pure planner: validate everything before making any writes. */
function plan360_(source, target, start, end) {
  const requiredHeaders = ['日期', '合作方', '运营位', '新增', '血量'];
  const headers = target[0] || [];
  if (headers.length !== new Set(headers).size || requiredHeaders.some(h => !headers.includes(h)))
    throw new Error('目标表表头缺失或重复');
  const positions = requiredHeaders.map(h => headers.indexOf(h));
  const [dateCol, partnerCol, surfaceCol, newCol] = positions;
  const sourceHeaders = source[0] || [];
  const sourceCols = SYNC360.surfaces.map((_, i) => {
    const name = '360-' + (i + 1);
    if (sourceHeaders.filter(h => h === name).length !== 1) throw new Error('源表表头缺失或重复: ' + name);
    return sourceHeaders.indexOf(name);
  });
  const existing = new Map();
  const latest = new Map();
  target.slice(1).forEach((row, i) => {
    if (blank360_(row[dateCol])) return;
    const day = day360_(row[dateCol]);
    const partner = String(row[partnerCol] || '').trim();
    const surface = String(row[surfaceCol] || '').trim();
    const key = key360_(day, partner, surface);
    if (existing.has(key)) throw new Error('目标表重复记录: ' + key);
    existing.set(key, {row: i + 2, value: row[newCol]});
    if (partner === '360' && !blank360_(row[newCol]))
      latest.set(surface, Math.max(latest.has(surface) ? latest.get(surface) : -Infinity, day));
  });
  const daily = [];
  source.slice(1).forEach(row => {
    if (blank360_(row[0])) return;
    let day;
    try { day = day360_(row[0]); } catch (_) { return; } // Summary rows are allowed.
    daily.push({day: day, values: row});
  });
  if (!daily.length) throw new Error('源表未找到每日数据');
  const first = Math.min(...daily.map(r => r.day));
  const required = new Map();
  SYNC360.surfaces.forEach(surface => {
    const rolling = Math.max(first, end - 13);
    const since = start !== null ? Math.max(first, start)
      : (latest.has(surface) ? Math.max(first, Math.min(rolling, latest.get(surface) + 1)) : rolling);
    for (let day = since; day <= end; day++) {
      const key = key360_(day, '360', surface);
      if (!existing.has(key) || blank360_(existing.get(key).value)) required.set(key, [day, surface]);
    }
  });
  const available = new Map();
  daily.forEach(record => {
    if (record.day > end || record.day < first) return;
    SYNC360.surfaces.forEach((surface, i) => {
      const key = key360_(record.day, '360', surface);
      if (!required.has(key)) return;
      const raw = record.values[sourceCols[i]];
      if (blank360_(raw) || String(raw).trim() === '') return;
      const text = String(raw).replace(/,/g, '').trim();
      if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text))
        throw new Error('无效新增数据: ' + iso360_(record.day) + '/' + surface);
      const value = Number(text);
      if (!Number.isFinite(value)) throw new Error('新增不是有限数值');
      if (i === 3 && value === 0) return;
      if (available.has(key)) throw new Error('源表重复记录: ' + key);
      available.set(key, value);
    });
  });
  const updates = [], appends = [], unavailable = [];
  Array.from(required).sort((a, b) => a[1][0] - b[1][0] || a[1][1].localeCompare(b[1][1])).forEach(([key, pair]) => {
    if (!available.has(key)) { unavailable.push(iso360_(pair[0]) + ' ' + pair[1]); return; }
    const value = available.get(key);
    if (existing.has(key)) {
      updates.push({range: "'合作方新增血量'!" + column360_(newCol) + existing.get(key).row, values: [[value]]});
    } else {
      const row = Array(headers.length).fill('');
      row[dateCol] = iso360_(pair[0]);
      row[partnerCol] = '360'; row[surfaceCol] = pair[1]; row[newCol] = value;
      appends.push(row);
    }
  });
  return {updates: updates, appends: appends, unavailable: unavailable};
}
