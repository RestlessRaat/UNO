import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const manifestPath = path.join(root, 'assets/manifest.json');
const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
const candidates = [process.env.UNO_CHROMIUM_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].filter(Boolean);
let executablePath;
for (const candidate of candidates) {
  try { await fs.access(candidate); executablePath = candidate; break; } catch {}
}
const browser = await chromium.launch({headless: true, executablePath});
try {
  const page = await browser.newPage();
  await page.goto('about:blank');
  await page.addScriptTag({path: path.join(here, 'node_modules/@scratch/scratch-svg-renderer/dist/web/scratch-svg-renderer.js')});
  let count = 0;
  for (const job of manifest.svg) {
    let source = await fs.readFile(path.join(root, 'sb3_assets', job.source), 'utf8');
    // Update instructions in the derived output; keep the source SB3 untouched.
    const isInstructions = Object.values(manifest.targets.instructions.costumes).some(c => c.source === job.source);
    const output = await page.evaluate(async ({svg, isInstructions}) => {
      if (isInstructions) {
        const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
        const walker = doc.createTreeWalker(doc, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) walker.currentNode.textContent = walker.currentNode.textContent.replace(/\b25\b/g, '35').replace(/more than 35/g, '35 or more');
        svg = new XMLSerializer().serializeToString(doc);
      }
      const renderer = new ScratchSVGRenderer.SVGRenderer();
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('SVG render timeout')), 15000);
        renderer.loadSVG(svg, false, () => { clearTimeout(timeout); resolve(); }).catch(reject);
      });
      renderer.draw(3);
      if (!renderer.canvas.width || !renderer.canvas.height) {
        renderer.canvas.width = 1;
        renderer.canvas.height = 1;
      }
      return {png: renderer.canvas.toDataURL('image/png').split(',')[1], size: renderer.size};
    }, {svg: source, isInstructions});
    await fs.writeFile(path.join(root, 'assets', job.output), Buffer.from(output.png, 'base64'));
    for (const target of Object.values(manifest.targets)) {
      for (const c of Object.values(target.costumes)) if (c.source === job.source) c.logical_size = output.size;
    }
    if (++count % 30 === 0) console.log(`Rendered ${count}/${manifest.svg.length}`);
  }
  // Bundle Scratch font for dynamic Latin text in the same family as the artwork.
  const fontDir = path.join(here, 'node_modules/scratch-render-fonts');
  async function findFonts(dir) {
    for (const ent of await fs.readdir(dir, {withFileTypes: true})) {
      const p = path.join(dir, ent.name);
      if (ent.isDirectory()) await findFonts(p);
      else if (/\.(ttf|otf)$/i.test(ent.name)) {
        await fs.mkdir(path.join(root, 'assets/fonts'), {recursive: true});
        await fs.copyFile(p, path.join(root, 'assets/fonts', ent.name));
      }
    }
  }
  await findFonts(fontDir);
  await fs.mkdir(path.join(root, 'assets/licenses'), {recursive: true});
  for (const [directory, name] of [[fontDir, 'scratch-render-fonts'], [path.join(here, 'node_modules/@scratch/scratch-svg-renderer'), 'scratch-svg-renderer']]) {
    for (const f of await fs.readdir(directory)) if (/^licen[cs]e/i.test(f)) {
      await fs.copyFile(path.join(directory, f), path.join(root, 'assets/licenses', `${name}-${f}`));
    }
  }
  manifest.renderer = {library: '@scratch/scratch-svg-renderer', scale: 3, browser: browser.version()};
  await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2));
  console.log(`Finished ${count} SVGs with Scratch fonts. Browser ${browser.version()}`);
} finally { await browser.close(); }
