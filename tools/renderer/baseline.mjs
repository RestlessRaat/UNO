// Run the original SB3 entirely locally to record visual/reference state.
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const out = path.join(root, 'build/baseline');
await fs.mkdir(out, {recursive: true});
const source = (await fs.readdir(root)).find(n => n.endsWith('.sb3'));
const packages = {vm: 'scratch-vm', render: 'scratch-render', storage: 'scratch-storage', svg: 'scratch-svg-renderer'};
const html = `<!doctype html><html><body style="margin:0;background:#111"><canvas id="stage" width="960" height="720" style="width:960px;height:720px"></canvas>
<script src="/storage/scratch-storage.js"></script><script src="/render/scratch-render.js"></script><script src="/svg/scratch-svg-renderer.js"></script><script src="/vm/scratch-vm.js"></script>
<script>
window.ready = (async () => {
 const canvas = document.querySelector('canvas');
 window.vm = new VirtualMachine();
 vm.attachStorage(new ScratchStorage.ScratchStorage());
 window.renderer = new ScratchRender(canvas);
 vm.attachRenderer(renderer);
 vm.attachV2BitmapAdapter(new ScratchSVGRenderer.BitmapAdapter());
 vm.setCompatibilityMode(true);
 vm.postIOData('userData', {username: 'PythonPort'});
 await vm.loadProject(await (await fetch('/project.sb3')).arrayBuffer());
 vm.start(); vm.greenFlag();
})();
</script></body></html>`;
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/') {res.setHeader('Content-Type', 'text/html'); res.end(html); return;}
    if (url.pathname === '/project.sb3') {res.end(await fs.readFile(path.join(root, source))); return;}
    const parts = url.pathname.split('/').filter(Boolean);
    const pkg = packages[parts.shift()];
    if (!pkg || parts.includes('..')) {res.writeHead(404); res.end(); return;}
    const base = path.join(here, 'node_modules/@scratch', pkg, 'dist/web');
    const file = path.resolve(base, ...parts);
    if (!file.startsWith(base + path.sep)) {res.writeHead(403); res.end(); return;}
    res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : 'application/octet-stream');
    res.end(await fs.readFile(file));
  } catch {res.writeHead(404); res.end();}
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({headless: true, executablePath: process.env.UNO_CHROMIUM_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
try {
  const page = await browser.newPage({viewport: {width: 960, height: 720}});
  page.on('pageerror', err => console.log('Source runtime:', err.message.slice(0, 200)));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.evaluate(() => window.ready);
  await page.waitForTimeout(1600);
  await page.screenshot({path: path.join(out, 'original-intro.png')});
  await page.waitForTimeout(7000);
  await page.screenshot({path: path.join(out, 'original-lobby.png')});
  const animations = process.argv.includes('--animations');
  if (animations) await page.evaluate(() => {
    window.animationTrace = [];
    const runtime = vm.runtime;
    const original = runtime._step.bind(runtime);
    runtime._step = function () {
      original();
      if (window.animationTrace.length >= 1500) return;
      const stage = runtime.getTargetForStage();
      const names = ['Uno.Transition counter', 'Uno.Transition frames', 'Uno.All Cards.Position.X',
        'Uno.All Cards.Position.Y', 'Uno.All Cards.Direction', 'Uno.All Cards.Source.x',
        'Uno.All Cards.Source.y', 'Uno.All Cards.Target.x', 'Uno.All Cards.Target.y',
        'Uno.All Cards.Source.r', 'Uno.All Cards.Target.r', 'Uno.Card indexes.Transition cards',
        'Uno.Card indexes.Current player', 'Uno.Card Indexes.Current Player', 'Uno.Intro over?'];
      const values = Object.fromEntries(Object.values(stage.variables).filter(v => names.includes(v.name))
        .map(v => [v.name, Array.isArray(v.value) ? v.value.slice() : v.value]));
      window.animationTrace.push({time: performance.now(), ...values});
    };
  });
  const started = await page.evaluate(() => {
    const stage = vm.runtime.getTargetForStage();
    const set = (target, name, value) => {const v = Object.values(target.variables).find(v => v.name === name); if (v) v.value = value;};
    set(stage, 'Lobby.Number of players in game', 4);
    set(stage, 'Layer.intro', -100);
    const lobby = vm.runtime.targets.find(t => t.isOriginal && t.sprite.name === 'lobby');
    const blocks = lobby.blocks._blocks;
    const proto = Object.values(blocks).find(b => b.opcode === 'procedures_prototype' && b.mutation.proccode === 'start ai game');
    const def = Object.values(blocks).find(b => b.opcode === 'procedures_definition' && b.inputs.custom_block.block === proto.id);
    vm.runtime._pushThread(def.next, lobby, {stackClick: true});
    return {procedure: proto.mutation.proccode};
  });
  console.log(JSON.stringify(started));
  if (animations) {
    for (let i = 0; i < 14; i++) {
      await page.waitForTimeout(1000);
      if ([0, 1, 3, 5, 7, 9, 13].includes(i)) await page.screenshot({path: path.join(out, `original-deal-${i + 1}s.png`)});
    }
    // Draw through the original project's own mouse IO and block scripts.
    const deck = await page.evaluate(() => {
      const stage = vm.runtime.getTargetForStage();
      const get = name => Object.values(stage.variables).find(v => v.name === name)?.value;
      const cards = get('Uno.Card Indexes.Deck');
      const index = Number(cards[cards.length - 1]) - 1;
      return {x: 240 + Number(get('Uno.All Cards.Position.X')[index]),
              y: 180 - Number(get('Uno.All Cards.Position.Y')[index]),
              current: get("Uno.Current player's turn"), local: get('Uno.Local player index')};
    });
    console.log('Reference deck', JSON.stringify(deck));
    if (Number(deck.current) === Number(deck.local)) {
      await page.waitForTimeout(1000);
      await page.mouse.move(deck.x * 2, deck.y * 2);
      await page.evaluate(({x, y}) => vm.postIOData('mouse', {x: x * 2, y: y * 2, canvasWidth: 960, canvasHeight: 720, isDown: true}), deck);
      await page.waitForTimeout(200);
      await page.evaluate(({x, y}) => vm.postIOData('mouse', {x: x * 2, y: y * 2, canvasWidth: 960, canvasHeight: 720, isDown: false}), deck);
      await page.waitForTimeout(220);
      await page.screenshot({path: path.join(out, 'original-draw-mid.png')});
      await page.waitForTimeout(1200);
      const playable = await page.evaluate(() => {
        const stage = vm.runtime.getTargetForStage();
        const get = name => Object.values(stage.variables).find(v => v.name === name)?.value;
        const cards = get('Uno.Card indexes.Current player');
        const legal = get('Uno.All Cards.Legal move?');
        const id = cards.find(id => legal[Number(id) - 1] === true);
        if (!id) return null;
        return {x: 240 + Number(get('Uno.All Cards.Position.X')[id - 1]) - 20,
                y: 180 - Number(get('Uno.All Cards.Position.Y')[id - 1])};
      });
      if (playable) {
        await page.evaluate(({x, y}) => vm.postIOData('mouse', {x: x * 2, y: y * 2, canvasWidth: 960, canvasHeight: 720, isDown: false}), playable);
        await page.waitForTimeout(400);
        await page.screenshot({path: path.join(out, 'original-hover.png')});
        await page.evaluate(() => vm.postIOData('mouse', {isDown: true}));
        await page.waitForTimeout(100);
        await page.evaluate(() => vm.postIOData('mouse', {isDown: false}));
        await page.waitForTimeout(150);
        await page.screenshot({path: path.join(out, 'original-play-mid.png')});
        await page.waitForTimeout(1100);
      }
    }
    const trace = await page.evaluate(() => window.animationTrace);
    await fs.writeFile(path.join(out, 'original-animation-trace.json'), JSON.stringify(trace));
  } else await page.waitForTimeout(14000);
  await page.screenshot({path: path.join(out, 'original-table.png')});
  const state = await page.evaluate(() => {
    const stage = vm.runtime.getTargetForStage();
    const vars = Object.fromEntries(Object.values(stage.variables).filter(v => /^(Uno\.|_Uno.Game type|uno\.)/.test(v.name)).map(v => [v.name, v.value]));
    return {stageEffects: stage.effects, variables: vars};
  });
  await fs.writeFile(path.join(out, 'original-state.json'), JSON.stringify(state, null, 2));
  console.log(JSON.stringify({effects: state.stageEffects, turn: state.variables["Uno.Current player's turn"], players: state.variables['Uno.Number of players']}));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
