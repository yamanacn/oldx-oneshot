// 渲染前检查：组件里"先 return 再调用 hook"。
// 这种写法平时看不出来，某一帧组件从"不画"变成"要画"时 React 报 #310（Rendered more hooks…），整条渲染失败。
// 规则：useNow / useVideoConfig / useMemo / useCurrentFrame 等 hook 一律写在组件最前面，早退的 return 放在它们后面。
// 用法：node tools/check-hooks.mjs [src]   有问题时退出码 1，并列出文件、组件和行号。
import fs from 'node:fs';
import path from 'node:path';

const root = process.argv[2] || 'src';
const files = [];
const walk = (d) => {
  for (const f of fs.readdirSync(d, {withFileTypes: true})) {
    const p = path.join(d, f.name);
    if (f.isDirectory()) walk(p);
    else if (/\.(tsx|ts)$/.test(f.name)) files.push(p);
  }
};
walk(root);

const problems = [];
for (const file of files) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    // 组件的开头：const Foo: React.FC<…> = (…) => {   或   export const Foo = <P …>(…) => {   或   function Foo(…) {
    const m = lines[i].match(/^\s*(?:export\s+)?(?:const|function)\s+([A-Z]\w*)\b.*(=>\s*\{|\)\s*\{)\s*$/);
    if (!m) continue;
    let depth = 0, returned = -1;
    for (let j = i; j < lines.length; j++) {
      const s = lines[j].replace(/\/\/.*$/, '').replace(/(['"`])(?:\\.|(?!\1).)*\1/g, '""');
      if (j > i && depth === 1) {
        if (/^\s*if\s*\(.*\)\s*return\b/.test(s) || /^\s*return\b/.test(s)) { if (returned < 0) returned = j; }
        else if (returned >= 0 && /\buse[A-Z]\w*\s*\(/.test(s)) problems.push(`${file}:${j + 1}  组件 ${m[1]}：第 ${returned + 1} 行已经可能 return，之后又调用了 hook`);
      }
      for (const ch of s) { if (ch === '{') depth++; else if (ch === '}') depth--; }
      if (j > i && depth <= 0) break;
    }
  }
}
if (problems.length) {
  console.error('hook 写在了早退的 return 之后（渲染到某一帧会报 React #310）：\n' + problems.join('\n'));
  process.exit(1);
}
console.log(`hook 顺序检查通过（${files.length} 个文件）`);
