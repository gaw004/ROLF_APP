// 把 src/page.html 里的 mermaid 源码渲染成内联 SVG，产出零依赖的
// ../data-and-flow.html —— 没有外部请求、没有 JS、双击就能开。
//
// 用法（依赖是临时的，不进 requirements、不进 git）：
//     cd docs/planning/diagrams/src
//     npm init -y && npm i mermaid@11 puppeteer-core
//     node build.mjs
//
// 需要本机装了 Chrome。渲染必须走真浏览器：mermaid 的排版依赖真实的字体度量，
// jsdom 量不出来，出来的图会缺字。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const here = path.dirname(fileURLToPath(import.meta.url));
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const EXPECTED_DIAGRAMS = 5;

const source = fs.readFileSync(path.join(here, 'page.html'), 'utf8');

// 一个临时页面：把 page.html 原样放进去，再用本地 mermaid 渲染它。
const scratch = path.join(here, '.build-preview.html');
fs.writeFileSync(scratch, `<!doctype html><html><head><meta charset="utf-8"></head><body>${source}
<script type="module">
import mermaid from './node_modules/mermaid/dist/mermaid.esm.min.mjs';
mermaid.initialize({ startOnLoad: false });
await mermaid.run({ nodes: [...document.querySelectorAll('pre.mermaid')] });
window.__done = true;
<\/script></body></html>`);

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--no-sandbox', '--allow-file-access-from-files'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1500, height: 1000 });
await page.goto('file://' + scratch, { waitUntil: 'networkidle0' });
await page.waitForFunction('window.__done === true', { timeout: 60000 });

// mermaid 给 svg 的是 width:100%，直接存下来会被容器压扁、字糊成一团。
// 按各自的 viewBox 钉死真实尺寸，让图版自己滚。
const svgs = await page.evaluate(() =>
  [...document.querySelectorAll('.plate svg')].map((svg) => {
    const box = svg.viewBox.baseVal;
    svg.setAttribute('width', Math.ceil(box.width));
    svg.setAttribute('height', Math.ceil(box.height));
    svg.removeAttribute('style');
    svg.setAttribute('role', 'img');
    return svg.outerHTML;
  }),
);

// 顺手做一次体检：标签有没有被格子裁掉。裁字不会报错，只是悄悄少一截。
const clipped = await page.evaluate(() => {
  let worst = 0;
  document.querySelectorAll('.plate svg foreignObject').forEach((box) => {
    const label = box.firstElementChild;
    if (!label) return;
    const need = Math.max(label.scrollWidth, Math.ceil(label.getBoundingClientRect().width));
    worst = Math.max(worst, need - Math.floor(box.width.baseVal.value));
  });
  return worst;
});
await browser.close();
fs.rmSync(scratch);

if (svgs.length !== EXPECTED_DIAGRAMS) {
  throw new Error(`期望 ${EXPECTED_DIAGRAMS} 张图，实际渲染出 ${svgs.length} 张 —— 多半是某一块 mermaid 语法没过`);
}
if (clipped > 2) {
  throw new Error(`有标签被裁掉 ${clipped}px —— 多半是给 mermaid 设了 fontFamily，见 README`);
}

let index = 0;
let out = source.replace(/<pre class="mermaid">[\s\S]*?<\/pre>/g, () => svgs[index++]);
out = out.replace(/\n<script>[\s\S]*?<\/script>\s*$/, '\n');   // 尺寸脚本换成了写死的尺寸

const cut = out.indexOf('</style>') + '</style>'.length;
fs.writeFileSync(path.join(here, '..', 'data-and-flow.html'), `<!doctype html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- 生成物，不要手改：改 src/page.html 再跑 src/build.mjs。 -->
<style>*,*::before,*::after{box-sizing:border-box}img{max-width:100%}</style>
${out.slice(0, cut)}
</head>
<body>
${out.slice(cut).trim()}
</body>
</html>
`);

console.log(`已生成 ../data-and-flow.html —— ${svgs.length} 张图，最大裁切 ${clipped}px`);
