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

// The provider catalog lives with the bot; the plugin ships a copy so it works when deployed alone.
export const catalog = {
  source: fileURLToPath(new URL('../../../src/plugins/chat_and_draw/providers.json', here)),
  copy: fileURLToPath(new URL('providers.json', here)),
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
  const source = fs.readFileSync(catalog.source, 'utf8');
  const copy = fs.existsSync(catalog.copy) ? fs.readFileSync(catalog.copy, 'utf8') : '';
  if (source !== copy) {
    if (check) { stale++; console.error('out of date:', catalog.copy); } else { fs.writeFileSync(catalog.copy, source); console.log('updated', catalog.copy); }
  }
  if (stale) process.exit(1);
}
