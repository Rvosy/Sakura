import { readFile, writeFile } from 'node:fs/promises';
const source = await readFile(new URL('../runtime-log/index.html', import.meta.url), 'utf8');
const html = source.replace('href="./styles.css"', 'href="../runtime-log/styles.css"')
  .replace('src="./runtime-log.js"', 'src="./bootstrap.js"');
if (html === source || html.includes('src="./runtime-log.js"')) throw new Error('日志页入口发生变化，请更新 Demo 构建脚本。');
await writeFile(new URL('./viewer.html', import.meta.url), html);
console.log('Demo viewer generated from the production log page.');
