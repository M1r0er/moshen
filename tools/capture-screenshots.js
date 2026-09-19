/**
 * 墨参 MoShen · 界面截图生成器
 * ----------------------------------------------------------------------------
 * 用 Electron 打开单文件产品原型（docs/产品原型.html，内置示例项目《长夜行》的
 * 离线样例数据），逐页截取界面截图，输出到 assets/screenshots/，供 README 引用。
 *
 * 说明：截图只使用原型里的示例数据，不读取任何本地工作区 / 用户小说项目。
 *
 * 用法：
 *   node docs/build-prototype.js                          # 先构建原型（如尚未构建）
 *   npx electron tools/capture-screenshots.js             # 生成全部截图
 *   npx electron tools/capture-screenshots.js --probe     # 只探测各页 DOM，不截图
 */
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..');
const PROTOTYPE = path.join(ROOT, 'docs', '产品原型.html');
const OUT_DIR = path.join(ROOT, 'assets', 'screenshots');
const PROBE = process.argv.includes('--probe');

const WIDTH = 1600;
const HEIGHT = 1000;
const SCALE = Number(process.env.SHOT_SCALE || 1);

app.commandLine.appendSwitch('force-device-scale-factor', String(SCALE));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const js = (win, code) => win.webContents.executeJavaScript(code, true);

async function waitFor(win, expr, timeout = 20000, interval = 200) {
  const t0 = Date.now();
  for (;;) {
    let ok = false;
    try {
      ok = await js(win, `(() => { try { return !!(${expr}); } catch (e) { return false; } })()`);
    } catch (e) { ok = false; }
    if (ok) return true;
    if (Date.now() - t0 > timeout) return false;
    await sleep(interval);
  }
}

async function clickSel(win, selector, index = 0) {
  return js(win, `(() => { const els = document.querySelectorAll(${JSON.stringify(selector)}); const el = els[${index}]; if (!el) return false; el.click(); return true; })()`);
}

async function clickText(win, selector, text) {
  return js(win, `(() => {
    const els = Array.from(document.querySelectorAll(${JSON.stringify(selector)}));
    const el = els.find((e) => (e.textContent || '').includes(${JSON.stringify(text)}));
    if (!el) return false;
    el.click();
    return true;
  })()`);
}

async function selectTab(win, label) {
  return clickText(win, '.tab-item', label);
}

const SCENES = [
  {
    file: 'chat.png',
    label: '对话',
    async run(win) {
      await selectTab(win, '对话');
      await waitFor(win, `document.querySelector('.conv-select')`);
      await js(win, `(() => {
        const sel = document.querySelector('.conv-select');
        if (!sel) return false;
        const opt = Array.from(sel.options).find((o) => o.value);
        if (!opt) return false;
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
      })()`);
      await waitFor(win, `document.querySelectorAll('.message-bubble').length >= 2`);
      await sleep(700);
    },
  },
  {
    file: 'writing.png',
    label: '写作',
    async run(win) {
      await selectTab(win, '写作');
      await waitFor(win, `document.querySelectorAll('.writing-vol-item').length > 0`, 15000);
      await js(win, `(() => {
        document.querySelectorAll('.writing-vol-item').forEach((item) => {
          const toggle = item.querySelector('.vol-toggle');
          if (toggle && toggle.textContent.trim() === '▶') item.querySelector('.writing-vol-header').click();
        });
      })()`);
      await waitFor(win, `document.querySelectorAll('.writing-chapter-item').length >= 6`, 10000);
      await clickText(win, '.writing-chapter-item', '雨夜归人');
      await waitFor(win, `document.querySelector('.writing-editor') && document.querySelector('.writing-editor').innerText.length > 80`, 10000);
      await sleep(900);
    },
  },
  {
    file: 'characters-stage.png',
    label: '角色-阶段图',
    async run(win) {
      await selectTab(win, '角色');
      await waitFor(win, `document.querySelectorAll('.char-item').length > 0`);
      await clickSel(win, '.char-item', 0);
      await waitFor(win, `document.querySelectorAll('.char-stage-group').length > 0`, 8000);
      await sleep(900);
    },
  },
  {
    file: 'characters-observations.png',
    label: '角色-AI 观察',
    async run(win) {
      await clickText(win, '.char-subtab', 'AI 观察');
      await sleep(900);
    },
  },
  {
    file: 'foreshadowing.png',
    label: '伏笔',
    async run(win) {
      await selectTab(win, '伏笔');
      await waitFor(win, `document.querySelectorAll('.fs-list-item').length > 0`);
      await clickSel(win, '.fs-list-item', 0);
      await sleep(900);
    },
  },
  {
    file: 'relations-graph.png',
    label: '星图-关系图',
    async run(win) {
      await selectTab(win, '星图');
      await waitFor(win, `document.querySelector('.rel-tabs')`);
      await clickText(win, '.rel-tabs button', '关系图');
      await waitFor(win, `document.querySelector('.rel-3d-canvas')`);
      await sleep(5000);
    },
  },
  {
    file: 'relations-archive.png',
    label: '星图-档案库',
    async run(win) {
      await clickText(win, '.rel-tabs button', '档案库');
      await sleep(1500);
    },
  },
  {
    file: 'settings.png',
    label: '设定',
    async run(win) {
      await selectTab(win, '设定');
      await waitFor(win, `document.querySelectorAll('.tree-node-row').length > 0`);
      await clickText(win, '.tree-node-row', '玄铁真气');
      await sleep(900);
    },
  },
  {
    file: 'outline.png',
    label: '大纲',
    async run(win) {
      await selectTab(win, '大纲');
      await waitFor(win, `document.querySelectorAll('.outline-node-group').length > 0`, 8000);
      await sleep(900);
    },
  },
  {
    file: 'plot-points.png',
    label: '爽爆点',
    async run(win) {
      await clickText(win, '.outline-subtab', '爽爆点');
      await waitFor(win, `document.querySelectorAll('.pp-card').length > 0`, 8000);
      await sleep(900);
    },
  },
  {
    file: 'knowledge.png',
    label: '知识库',
    async run(win) {
      await selectTab(win, '知识库');
      await waitFor(win, `document.querySelectorAll('.knowledge-item').length > 0`);
      await clickSel(win, '.knowledge-item', 0);
      await sleep(900);
    },
  },
  {
    file: 'files.png',
    label: '文件',
    async run(win) {
      await selectTab(win, '文件');
      await waitFor(win, `document.querySelectorAll('.file-card').length > 0`);
      await sleep(900);
    },
  },
  {
    file: 'inspiration.png',
    label: '灵感',
    async run(win) {
      await selectTab(win, '灵感');
      await waitFor(win, `document.querySelectorAll('.inspiration-file-item').length > 0`);
      await clickSel(win, '.inspiration-file-item', 0);
      await sleep(900);
    },
  },
];

async function createWin() {
  const win = new BrowserWindow({
    width: WIDTH,
    height: HEIGHT,
    show: false,
    skipTaskbar: true,
    backgroundColor: '#ffffff',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  await win.loadFile(PROTOTYPE);
  return win;
}

async function main() {
  if (!fs.existsSync(PROTOTYPE)) {
    throw new Error('未找到产品原型：' + PROTOTYPE + '\n请先运行 node docs/build-prototype.js');
  }

  const win = await createWin();
  win.webContents.on('console-message', (_e, level, message) => {
    if (level >= 2) console.error('[renderer]', message);
  });
  const ready = await waitFor(win, `document.querySelectorAll('.tab-item').length >= 10`);
  if (!ready) throw new Error('原型界面未挂载：未找到一级页签');
  await waitFor(win, `document.querySelector('.project-list .project-item.active')`, 20000);
  await sleep(1200);

  if (PROBE) {
    const report = {};
    for (const scene of SCENES) {
      try {
        await scene.run(win);
        report[scene.label] = await js(win, `(() => {
          const counts = {};
          document.querySelectorAll('.tab-content *').forEach((el) => {
            const cn = el.className;
            if (typeof cn === 'string' && cn) {
              cn.split(/\\s+/).forEach((c) => { if (c) counts[c] = (counts[c] || 0) + 1; });
            }
          });
          return counts;
        })()`);
      } catch (e) {
        report[scene.label] = { __error: String((e && e.message) || e) };
      }
    }
    fs.writeFileSync(path.join(ROOT, 'tools', '_probe.json'), JSON.stringify(report, null, 2), 'utf8');
    console.log('探测报告已写入 tools/_probe.json');
    app.quit();
    return;
  }

  fs.mkdirSync(OUT_DIR, { recursive: true });
  for (const scene of SCENES) {
    try {
      await scene.run(win);
      // 强制重绘：隐藏窗口下 DOM 更新后合成帧可能滞后，show() + offsetHeight 触发重排重绘
      if (!win.isVisible()) win.showInactive();
      await js(win, `document.body.offsetHeight`);
      await sleep(300);
      const image = await win.webContents.capturePage();
      const png = image.toPNG();
      fs.writeFileSync(path.join(OUT_DIR, scene.file), png);
      console.log(`✓ ${scene.file}  (${(png.length / 1024).toFixed(0)} KB)`);
    } catch (e) {
      console.error(`✗ ${scene.file}: ${(e && e.message) || e}`);
    }
  }
  console.log('截图输出目录：' + OUT_DIR);
  app.quit();
}

app.whenReady().then(() => {
  main().catch((e) => {
    console.error('失败：', (e && e.message) || e);
    app.exit(1);
  });
});

app.on('window-all-closed', () => app.quit());
