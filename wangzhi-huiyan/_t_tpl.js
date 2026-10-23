// 功能10：自定义网址预设模板 —— 自测
// 覆盖：内置模板锁定、自定义 CRUD、去重、多选批量填入、localStorage、弹窗、样式
// 用法：node _t_tpl.js
const fs = require('fs');
const E = require('./engine.js');
const html = fs.readFileSync('index.html', 'utf-8');

let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,300) : '')); } }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

sec('1. 内置模板数据');
ok('BUILTIN_TEMPLATES 定义', /const BUILTIN_TEMPLATES = \[/.test(html));
ok('含 9 个内置样例', (html.match(/id: 'b[0-9]'/g) || []).length === 9, (html.match(/id: 'b[0-9]'/g) || []).length);
ok('内置模板带锁定语义 builtin', /builtin: true/.test(html));
ok('TEMPLATES_KEY 约定', E.TEMPLATES_KEY === 'wzh_templates');

sec('2. 模板 CRUD（localStorage）');
ok('getCustomTemplates 读 localStorage', /function getCustomTemplates\(\)[\s\S]*?getItem\(E\.TEMPLATES_KEY\)/.test(html));
ok('saveCustomTemplates 写 localStorage', /function saveCustomTemplates\([\s\S]*?setItem\(E\.TEMPLATES_KEY/.test(html));
ok('新增模板（id 时间戳）', /id: 'tpl_' \+ Date\.now\(\)/.test(html));
ok('编辑模板（改 name/url）', /function saveTplForm\(\)[\s\S]*?list\[i\]\.name = name[\s\S]*?list\[i\]\.url = url/.test(html));
ok('删除模板（filter）', /function deleteTpl\([\s\S]*?filter\(function \(x\) \{ return x\.id !== id/.test(html));
ok('同 url 去重（不重复新增）', /list\.some\(function \(x\) \{ return x\.url === url; \}\)[\s\S]*?已存在同名模板/.test(html));

sec('3. 渲染模板 chips');
ok('renderTemplates 函数', /function renderTemplates\(\)/.test(html));
ok('内置+自定义合并渲染', /BUILTIN_TEMPLATES\.map[\s\S]*?\.concat\(custom\.map/.test(html));
ok('内置点击=填入+检测', /dataset\.builtin === '1'\) run\(\)/.test(html));
ok('管理模板按钮', /chip-manage[\s\S]*?openTplModal/.test(html) || html.includes('⚙️ 管理模板'));
ok('自定义模板带📌标记', html.includes("(t.builtin ? '' : '📌 ')"));

sec('4. 管理弹窗');
ok('tplModal 弹窗存在', html.includes('id="tplModal"'));
ok('复用 .modal overlay', html.includes('class="modal tplmodal"'));
ok('新增模板表单', html.includes('id="tplForm"'));
ok('模板列表容器', html.includes('id="tplList"'));
ok('内置模板在弹窗显示🔒', /t\.builtin \? ' <span class="tpl-lock">🔒 内置<\/span>'/.test(html) || html.includes('🔒 内置'));
ok('内置模板无编辑/删除按钮', /t\.builtin\s*\?\s*''\s*:\s*'<button class="hact" onclick="editTpl/.test(html));
ok('弹窗点击遮罩关闭', /\$\(tplModal\)'\)\.addEventListener\('click'/.test(html) || /\$\('tplModal'\)\.addEventListener\('click'/.test(html));

sec('5. 交互：点击填入 + 多选批量');
ok('点击模板填入输入框', /\$\('ta'\)\.value = c\.dataset\.v/.test(html));
ok('多选批量填入函数', /function fillSelectedTpls\(\)/.test(html));
ok('批量填入用换行分隔', /urls\.join\('\\n'\)/.test(html));
ok('批量填入提示', /已填入 ' \+ urls\.length \+ ' 个网址/.test(html));

sec('6. 样式（CSS 变量）');
ok('.tpl-item 用 panel2', /\.tpl-item\{[^}]*var\(--panel2\)/.test(html));
ok('.tpl-form input 用 panel2', /\.tpl-form input\{[^}]*var\(--panel2\)/.test(html));
ok('checkbox 有 accent-color', /\.tpl-chk\{[^}]*accent-color:var\(--blue\)/.test(html));
ok('管理按钮有 hover', /\.chip\.chip-manage:hover/.test(html));

sec('7. 初始化与持久化');
ok('页面加载 renderTemplates', /renderTemplates\(\)/.test(html));
ok('自定义模板存 localStorage', /localStorage\.setItem\(E\.TEMPLATES_KEY/.test(html));

sec('8. 红线');
ok('模板不传后端（无 requestJSON/postJSON）', !/function (getCustomTemplates|saveCustomTemplates|renderTemplates|saveTplForm)\((?:(?!\n\})[\s\S])*?(requestJSON|postJSON)\(/.test(html));
ok('内置样例数据与原先一致（形近钓鱼站等）', html.includes('样例1 · 形近钓鱼站(高危)') && html.includes('我想找翻墙工具'));
ok('历史/批量/导出/AI解读/复制/二维码/收藏关键函数仍在', ['loadHistory','batchDetect','doExport','aiInterpretFromRow','copyLink','openQrModal','toggleFavorite','applyFilterTags'].every(k => html.includes(k) || E[k]));
ok('无新增 .py 文件', fs.readdirSync('.').filter(x => x.endsWith('.py')).join(',') === 'serve.py');

console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail ? 1 : 0);
