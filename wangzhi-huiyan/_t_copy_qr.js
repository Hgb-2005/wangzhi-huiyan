// 功能7：快捷复制 + 二维码预览 —— 自测
// 覆盖：二维码编码器正确性（黄金矩阵对拍）、版本自动选择、边界、
//       三处入口按钮、弹窗与遮罩、复制逻辑、CSS 变量、红线
// 用法：node _t_copy_qr.js
const fs = require('fs');
const E = require('./engine.js');
const html = fs.readFileSync('index.html', 'utf-8');
const engine = fs.readFileSync('engine.js', 'utf-8');

let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,300) : '')); } }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

// ---- 黄金矩阵：hello world / M（已与 Python qrcode 逐模块对拍一致）----
const GOLDEN_HELLO = [
  '111111101101101111111',
  '100000101111101000001',
  '101110100001101011101',
  '101110101110101011101',
  '101110100111001011101',
  '100000100111101000001',
  '111111101010101111111',
  '000000001111100000000',
  '101101110011101001011',
  '011011010101111111101',
  '000110111011010100011',
  '011111010011000101010',
  '000101111100111000001',
  '000000001111001110100',
  '111111101111111110000',
  '100000101000010101111',
  '101110100100100001100',
  '101110101010001001110',
  '101110101100100100100',
  '100000100011011110001',
  '111111101011011100100'
];

sec('1. 二维码编码器：黄金矩阵对拍');
const hello = E.buildQrMatrix('hello world', { ecl: 'M' });
ok('返回 ok:true', hello.ok === true);
ok('版本 = 1', hello.version === 1, hello.version);
ok('边长 = 21', hello.size === 21, hello.size);
let matDiff = 0;
for (let i = 0; i < 21; i++) {
  const row = hello.matrix[i].map(x => (x ? '1' : '0')).join('');
  if (row !== GOLDEN_HELLO[i]) { matDiff++; if (matDiff === 1) console.log('  首行差异@' + i + ':\n    期望 ' + GOLDEN_HELLO[i] + '\n    实际 ' + row); }
}
ok('矩阵与黄金矩阵逐模块一致', matDiff === 0, matDiff + ' 行不一致');

sec('2. 版本自动选择（长网址自适应）');
// 字节容量：v1 L=17, v2 L=32, v3 L=53 ...
ok('短网址 → v1/v2 小版本', E.buildQrMatrix('https://a.com/x').version <= 2, E.buildQrMatrix('https://a.com/x').version);
ok('中等网址 → 自动升版本', E.buildQrMatrix('https://example.com/' + 'x'.repeat(100)).version >= 6);
ok('长网址(800) → 高版本', E.buildQrMatrix('https://example.com/' + 'x'.repeat(800)).version >= 20);
ok('超长(2900) → v40 + 降级 L', (function () { const r = E.buildQrMatrix('https://example.com/' + 'x'.repeat(2900)); return r.ok && r.version === 40 && r.ecl === 'L'; })());
// 中文 UTF-8 字节数正确
const zh = E.buildQrMatrix('https://example.com/中文路径');
ok('中文按 UTF-8 编码（字节>字符数）', zh.ok && zh.version >= 3, zh.version);

sec('3. 边界与错误处理');
const empty = E.buildQrMatrix('');
ok('空串返回失败', !empty.ok && /为空/.test(empty.msg), empty.msg);
const tooLong = E.buildQrMatrix('x'.repeat(3000));
ok('超上限返回失败并说明', !tooLong.ok && /过长/.test(tooLong.msg), tooLong.msg);
const matrixOk = E.buildQrMatrix('ok');
ok('矩阵是二维布尔数组', Array.isArray(matrixOk.matrix) && matrixOk.matrix.length === matrixOk.size && matrixOk.matrix.every(r => Array.isArray(r) && r.length === matrixOk.size));
ok('矩阵含功能图案（四角定位）', matrixOk.matrix[0][0] === true && matrixOk.matrix[0][matrixOk.size-1] === true && matrixOk.matrix[matrixOk.size-1][0] === true);

sec('4. 三处入口按钮');
// 单条报告
ok('单条报告有复制链接按钮', html.includes('🔗 复制链接'));
ok('单条报告有预览二维码按钮', html.includes('📱 预览二维码'));
ok('单条按钮与 AI解读并排（同一 actions 容器）', /\$\{cpQrBtns\}[\s\S]{0,300}AI解读详情/.test(html));
// 批量表格（两处渲染函数都要有）
ok('批量表格有复制按钮', (html.match(/🔗 复制</g) || []).length >= 2, (html.match(/🔗 复制</g) || []).length);
ok('批量表格有二维码按钮', (html.match(/📱 二维码</g) || []).length >= 2, (html.match(/📱 二维码</g) || []).length);
// 历史表格
ok('历史表格有复制按钮', html.includes('<button class="hact" data-url='));
ok('历史表格有二维码按钮', html.includes('onclick="openQrFrom(this)"'));
ok('历史复制/二维码/AI解读并排', /data-url="\$\{escapeHtml\(it\.originalUrl[\s\S]{0,200}reuseHistory/.test(html));

sec('5. 复制逻辑');
ok('copyLink 函数存在', /async function copyLink\(/.test(html));
ok('优先用 navigator.clipboard', html.includes('navigator.clipboard'));
ok('有 execCommand 降级', html.includes('execCommand'));
ok('成功提示「复制成功」', html.includes("'复制成功'"));
ok('失败提示「复制失败，请手动选中」', html.includes('复制失败，请手动选中'));
ok('从 data-url 取原始网址（不拼进 onclick）', /function copyLinkFrom\(/.test(html));

sec('6. 二维码弹窗');
ok('qrModal 弹窗存在', html.includes('id="qrModal"'));
ok('复用 .modal 遮罩类', html.includes('class="modal qrmodal"'));
ok('有 canvas 画布', html.includes('id="qrCanvas"'));
ok('有关闭按钮', html.includes('onclick="closeQrModal()"'));
ok('有说明文字「扫描访问该链接」', html.includes('扫描访问该链接'));
ok('openQrModal 调用引擎', html.includes('E.buildQrMatrix(url)'));
ok('点击遮罩关闭', html.includes("$('qrModal').addEventListener('click'"));
ok('Esc 也关闭 qrModal', html.includes("$('qrModal').classList.contains('on')"));
ok('画布白底黑块（QR 扫描要求）', html.includes("fillStyle = '#fff'") && html.includes("fillStyle = '#000'"));
ok('长网址自适应边长（scale 动态计算）', html.includes('Math.max(2, Math.floor(280'));

sec('7. CSS 变量与双主题');
const qrCssStart = html.indexOf('功能7：快捷复制');
const qrCssEnd = html.indexOf('.qrmodal .mbox');
const qrCss = html.slice(qrCssStart, qrCssEnd + 200);
ok('功能7 CSS 定位成功', qrCssStart > 0 && qrCssEnd > qrCssStart);
const qrHex = qrCss.replace(/\/\*[\s\S]*?\*\//g, '').match(/#[0-9a-fA-F]{3,6}\b/g);
ok('功能7 CSS 无硬编码颜色（画布白底是扫描要求，单独说明）', !qrHex, qrHex && qrHex.join(','));
ok('弹窗文字用 CSS 变量', /\.qr-hint\{[^}]*var\(--text2\)/.test(html) && /\.qr-url\{[^}]*var\(--text3\)/.test(html));
ok('按钮复用现有类（.abtn/.aibtn/.hact）', /class="abtn" data-url/.test(html) && /class="aibtn" data-url/.test(html) && /class="hact" data-url/.test(html));
ok('row-actions 有 gap 间距', /\.row-actions\{[^}]*gap:5px/.test(html));

sec('8. 联动：按钮跟随筛选显隐');
// 按钮都在 shown.map 生成的 <tr> 里，筛选过滤行时按钮自然消失
ok('历史按钮在行模板内', /shown\.map\(it => `[\s\S]*?复制[\s\S]*?openQrFrom/.test(html));
ok('批量按钮在 shown.map 内', /shown\.map\(r => \{[\s\S]*?copyLinkFrom/.test(html));

sec('9. 红线：不改后端、不改已完工逻辑');
ok('无新增后端接口调用', !html.includes("'/qr") && !html.includes("'/copy") && !html.includes("'/qrcode"));
ok('二维码纯前端（无 fetch/requestJSON）', !/requestJSON\([\s\S]{0,60}qr/.test(html));
ok('引擎只新增 buildQrMatrix 一个导出', Object.keys(E).filter(k => /qr/i.test(k)).length === 1, Object.keys(E).filter(k => /qr/i.test(k)).join(','));
ok('buildQrMatrix 是纯函数（Node 可跑）', typeof E.buildQrMatrix === 'function');
ok('历史/主题/批量/导出/AI解读/筛选 关键函数仍在',
   ['loadHistory','toggleTheme','batchDetect','doExport','aiInterpretFromRow','applyFilterTags','buildFilterBarHtml']
     .every(k => html.includes(k)));
ok('AI解读按钮未被移除', html.includes('🤖 AI解读'));
ok('复制/二维码不调后端（copyLink/openQrModal 内无 requestJSON/postJSON）',
   !/function copyLink\((?:(?!\n\})[\s\S])*?(?:requestJSON|postJSON)\(/.test(html)
   && !/function openQrModal\((?:(?!\n\})[\s\S])*?(?:requestJSON|postJSON)\(/.test(html));

console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail ? 1 : 0);
