// Inline ui/base.css and ui/base.js into every admin page.
// NapCat serves each page as a single HTML file, so shared code is copied in at build time.
//   node build-ui.mjs          rewrite pages
//   node build-ui.mjs --check  exit 1 if any page is out of date (used by the tests)
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';

const here = new URL('./', import.meta.url);
export const pages = ['models.html', 'participation.html', 'personas.html', 'proactive.html',
  '../../../src/plugins/chat_and_draw/manage.html'].map(p => fileURLToPath(new URL(p, here)));
const shared = {
  css: fs.readFileSync(new URL('ui/base.css', here), 'utf8').trim(),
  js: fs.readFileSync(new URL('ui/base.js', here), 'utf8').trim(),
};

export function render(html) {
  const style = /(<style data-shared="base\.css">)[\s\S]*?(<\/style>)/;
  const script = /(<script data-shared="base\.js">)[\s\S]*?(<\/script>)/;
  if (!style.test(html) || !script.test(html)) throw new Error('page is missing data-shared markers');
  return html.replace(style, (_, open, close) => `${open}\n${shared.css}\n${close}`)
    .replace(script, (_, open, close) => `${open}\n${shared.js}\n${close}`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1])) {
  const check = process.argv.includes('--check');
  let stale = 0;
  for (const page of pages) {
    const html = fs.readFileSync(page, 'utf8');
    const next = render(html);
    if (next === html) continue;
    if (check) { stale++; console.error('out of date:', page); } else { fs.writeFileSync(page, next); console.log('updated', page); }
  }
  if (stale) process.exit(1);
}
