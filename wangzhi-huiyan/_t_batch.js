// 功能6 补充测试：批量区 DOM 交互（rerenderBatchTable）
// 目的：验证「批量表格重渲染」这条最复杂的路径 ——
//   筛选栏替换、表格替换、AI解读下标不错位、无匹配友好提示
// 用法：node _t_batch.js
const fs = require('fs');
const E = require('./engine.js');
const html = fs.readFileSync('index.html', 'utf-8');

let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,400) : '')); } }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

function grabFn(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}', 'm');
  const m = html.match(re);
  return m ? m[0] : null;
}

// ---- 构造一个最小但真实的 DOM 模拟 ----
// 需要支持：querySelectorAll('.answer') / querySelector('h3') / innerHTML / insertAdjacentHTML / remove
// 关键：querySelector 返回的子节点必须是【挂在父节点上】的活对象，
// 否则 insertAdjacentHTML 写进的是游离节点，测不到替换逻辑。
function makeEl(tag, cls, parent) {
  const el = {
    tag, className: cls || '', _html: '', parent: parent || null, _removed: false,
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; },
    get textContent() { return this._html.replace(/<[^>]*>/g, ''); },
    querySelector(sel) {
      if (sel === 'h3') {
        const m = this._html.match(/<h3[^>]*>([\s\S]*?)<\/h3>/);
        if (!m) return null;
        const h = makeEl('h3', '', this);
        h._html = m[0];
        h._text = m[1];
        return h;
      }
      if (sel.startsWith('.')) {
        const c = sel.slice(1);
        let has = false, snippet = '';
        if (c === 'fbar') {
          const m = this._html.match(/<div class="fbar"[\s\S]*?<\/div>\s*(?=<div|<table|$)/);
          has = !!m; snippet = m ? m[0] : '';
        } else if (c === 'htable-wrap') {
          const m = this._html.match(/<div class="htable-wrap">[\s\S]*?<\/table>\s*<\/div>/);
          has = !!m; snippet = m ? m[0] : '';
        } else if (c === 'hist-empty') {
          const m = this._html.match(/<div class="hist-empty">[\s\S]*?<\/div>/);
          has = !!m; snippet = m ? m[0] : '';
        }
        if (!has) return null;
        const child = makeEl('div', c, this);
        child._html = snippet;
        return child;
      }
      return null;
    },
    querySelectorAll(sel) { return []; },
    insertAdjacentHTML(pos, h) {
      // 写到父节点上（模拟真实 DOM 的兄弟插入）
      const p = this.parent;
      if (!p) { this._html = this._html + h; return; }
      if (pos === 'afterend') {
        // 找到自己在父节点 _html 中的位置，插到后面
        const idx = p._html.indexOf(this._html);
        if (idx < 0) { p._html = p._html + h; return; }
        p._html = p._html.slice(0, idx + this._html.length) + h + p._html.slice(idx + this._html.length);
      } else if (pos === 'afterbegin') {
        p._html = h + p._html;
      } else if (pos === 'beforeend') {
        p._html = p._html + h;
      }
    },
    remove() {
      this._removed = true;
      const p = this.parent;
      if (p && this._html) {
        const i = p._html.indexOf(this._html);
        if (i >= 0) p._html = p._html.slice(0, i) + p._html.slice(i + this._html.length);
      }
    },
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    getAttribute: () => null, setAttribute(){}, addEventListener(){}
  };
  return el;
}

const answerEls = [];
const sandbox = `
const E = require('${__dirname.replace(/\\/g,'/')}/engine.js');
let filterTags = [];
let lastBatchRows = null;
let histRawItems = [];
const __dom = {};
const $ = id => {
  if (id === 'out') return { querySelectorAll: sel => (sel === '.answer' ? __answers : []) };
  if (!__dom[id]) __dom[id] = { id, innerHTML:'', textContent:'', outerHTML:'',
    classList:{add(){},remove(){},toggle(){},contains(){return false}},
    getAttribute:()=>null, setAttribute(){}, addEventListener(){} };
  return __dom[id];
};
let __answers = [];
function setAnswers(arr) { __answers = arr; }
function showExpTip(){}
function refreshExportMenuCount(){}
function isFavUrl(){ return false; }
function toggleFavoriteFrom(){}
${grabFn('escapeHtml')}
${grabFn('escapeAttr')}
${grabFn('buildFilterBarHtml')}
${grabFn('rerenderBatchTable')}
module.exports = {
  buildFilterBarHtml, rerenderBatchTable, setAnswers,
  setTags: t => { filterTags = t; }, setBatch: d => { lastBatchRows = d; },
  getTags: () => filterTags,
  getHtml: id => ($(id) ? $(id).innerHTML : null)
};
`;
fs.writeFileSync('_sb_b.js', sandbox);
const SB = require('./_sb_b.js');

// ---- 真实形态的批量数据 ----
const batchData = [
  { url:'https://a.com/',           level:'safe',       label:'安全', hops:0 },
  { url:'https://b.com/',           level:'suspicious', label:'可疑', hops:0 },
  { url:'http://c.top/x',           level:'danger',     label:'高危', hops:3 },
  { url:'https://d.com/',           level:'unknown',    label:'未知', hops:0, note:'页面抓取失败，无法访问目标站点' },
  { url:'https://e.link/x',         level:'unknown',    label:'未知', hops:0, note:'短链解析失败' },
  { url:'https://f.top/y',          level:'suspicious', label:'可疑', hops:4, isLoop:true }
];
const mkAnswer = () => {
  const el = makeEl('div', 'answer');
  el.innerHTML = '<h3>📋 批量检测结果</h3><div class="htable-wrap"><table class="htable"><tbody>' +
    batchData.map((r,i)=>`<tr><td><button class="bt-url">${r.url}</button></td><td><span class="lv ${r.level}">${r.label}</span></td><td><button class="aibtn" onclick="aiInterpretFromRow(window.__wzhBatchRows[${i}], this)">🤖 AI解读</button></td></tr>`).join('') +
    '</tbody></table></div>';
  return el;
};

sec('1. 批量表格重渲染：筛选栏 + 表格一起替换');
SB.setBatch(batchData);
SB.setTags([]);
const ans = mkAnswer();
SB.setAnswers([ans]);
SB.rerenderBatchTable();
const h = ans.innerHTML;
ok('筛选栏已插入', h.includes('class="fbar"'));
ok('表格已重建', h.includes('<table class="htable">'));
ok('标题保留', h.includes('📋 批量检测结果'));
const tb = (h.match(/<tbody>[\s\S]*?<\/tbody>/) || [''])[0];
ok('未筛选 6 行数据', (tb.match(/<tr>/g)||[]).length === 6, (tb.match(/<tr>/g)||[]).length);

sec('2. AI解读下标不错位（筛选后仍指向原始行）');
SB.setTags(['danger']);
const ans2 = mkAnswer();
SB.setAnswers([ans2]);
SB.rerenderBatchTable();
const h2 = ans2.innerHTML;
const tb2 = (h2.match(/<tbody>[\s\S]*?<\/tbody>/) || [''])[0];
ok('筛选高危后 1 行', (tb2.match(/<tr>/g)||[]).length === 1, (tb2.match(/<tr>/g)||[]).length);
const idx = (tb2.match(/__wzhBatchRows\[(\d+)\]/) || [])[1];
ok('AI解读下标 = 2（c.top 在原始数组里的位置）', idx === '2', idx);
ok('该行是高危那条', tb2.includes('c.top/x'));
ok('不含其他行', !tb2.includes('a.com') && !tb2.includes('e.link/x'));

sec('3. 筛选后 AI 下标仍正确（多选场景）');
SB.setTags(['danger','safe']);
const ans3 = mkAnswer();
SB.setAnswers([ans3]);
SB.rerenderBatchTable();
const tb3 = (ans3.innerHTML.match(/<tbody>[\s\S]*?<\/tbody>/) || [''])[0];
const idxs = (tb3.match(/__wzhBatchRows\[(\d+)\]/g) || []).map(s => s.match(/\[(\d+)\]/)[1]);
ok('筛出 2 行', (tb3.match(/<tr>/g)||[]).length === 2, (tb3.match(/<tr>/g)||[]).length);
ok('下标分别为 0 和 2（原始位置）', JSON.stringify(idxs) === '["0","2"]', JSON.stringify(idxs));
ok('含 a.com 与 c.top', tb3.includes('a.com') && tb3.includes('c.top/x'));

sec('4. 批量区无匹配 → 友好提示');
// 多选是「或」语义，所以零匹配要造一份「没有任何标签能命中」的数据：
// 这批数据只有一条 safe，其余特征标签（跳数、备注、循环）全都不满足。
const noHitData = [{ url:'https://only-safe.com/', level:'safe', label:'安全', hops:0 }];
SB.setBatch(noHitData);
SB.setTags(['hopRisk','fetchFail','parseFail','redirect']);
const ans4 = mkAnswer();
SB.setAnswers([ans4]);
SB.rerenderBatchTable();
ok('提示文案正确', ans4.innerHTML.includes('没有符合当前筛选条件的记录'), ans4.innerHTML.slice(0,300));
ok('不渲染表格', !ans4.innerHTML.includes('<table class="htable">'));
ok('筛选栏仍在（可取消筛选）', ans4.innerHTML.includes('class="fbar"'));
ok('筛选栏显示 0 条命中', ans4.innerHTML.includes('已筛选 0 / 1 条'), (ans4.innerHTML.match(/已筛选[^<]*/)||[''])[0]);
// 恢复批量数据供后续用例使用
SB.setBatch(batchData);

sec('5. 清空后恢复全量');
SB.setTags([]);
const ans5 = mkAnswer();
SB.setAnswers([ans5]);
SB.rerenderBatchTable();
const tb5 = (ans5.innerHTML.match(/<tbody>[\s\S]*?<\/tbody>/) || [''])[0];
ok('恢复 6 行', (tb5.match(/<tr>/g)||[]).length === 6, (tb5.match(/<tr>/g)||[]).length);
const idx5 = (tb5.match(/__wzhBatchRows\[(\d+)\]/g) || []).map(s => s.match(/\[(\d+)\]/)[1]);
ok('下标恢复为 0..5', JSON.stringify(idx5) === '["0","1","2","3","4","5"]', JSON.stringify(idx5));

sec('6. 无批量数据时的兜底');
SB.setBatch(null);
const ans6 = mkAnswer();
SB.setAnswers([ans6]);
SB.rerenderBatchTable();
ok('显示"没有可展示的结果"', ans6.innerHTML.includes('没有可展示的结果'), ans6.innerHTML.slice(0,150));

sec('7. 找的是最后一个批量结果卡片');
SB.setBatch(batchData);
const old = mkAnswer();
const last = mkAnswer();
SB.setAnswers([old, last]);
SB.setTags(['danger']);
SB.rerenderBatchTable();
ok('旧卡片未被改动', old.innerHTML.includes('<tbody>') && (old.innerHTML.match(/<tr>/g)||[]).length === 6, (old.innerHTML.match(/<tr>/g)||[]).length);
ok('最后一张卡片被重渲染', (last.innerHTML.match(/<tbody>[\s\S]*?<\/tbody>/)[0].match(/<tr>/g)||[]).length === 1);

sec('8. 批量区特征标签筛选（引擎层）');
for (const [k, exp] of [['fetchFail',1],['parseFail',1],['redirect',2],['hopRisk',2]]) {
  const n = E.applyFilterTags(batchData, [k], 'batch').length;
  ok(k + ' → ' + exp + ' 条', n === exp, n);
}
ok('高危+安全 = 2 条', E.applyFilterTags(batchData, ['danger','safe'],'batch').length === 2);
ok('空筛选返回原数组引用（不复制）', E.applyFilterTags(batchData, [], 'batch') === batchData);

fs.unlinkSync('_sb_b.js');
console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail?1:0);
