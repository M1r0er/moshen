/**
 * 墨参 MoShen · Electron 主进程
 * 负责启动 Python 后端服务、创建桌面窗口、管理应用生命周期
 */
const { app, BrowserWindow, shell, globalShortcut, dialog, ipcMain, session } = require('electron');
const path = require('path');
const net = require('net');
const { spawn, execSync } = require('child_process');
const fs = require('fs');

// 设置 userData 路径（确保单实例锁和缓存路径一致）
app.setPath('userData', path.join(app.getPath('appData'), 'moshen'));

// 单实例锁：防止多个墨参同时运行
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
  process.exit(0);
}

// 是否为开发模式
const isDev = process.argv.includes('--dev');

// Python 后端进程
let pythonProcess = null;
// 主窗口
let mainWindow = null;

// 后端服务端口
let backendPort = 8765;

/**
 * 查找可用端口
 */
function findFreePort(startPort = 8765, endPort = 8780) {
  return new Promise((resolve) => {
    for (let port = startPort; port <= endPort; port++) {
      const server = net.createServer();
      server.listen(port, '127.0.0.1', () => {
        server.close(() => resolve(port));
      });
      server.on('error', () => {
        if (port === endPort) resolve(startPort);
      });
    }
  });
}

/**
 * 询问启动页：还要等多久才能让用户看到完整一轮动画
 *
 * 后端就绪可能只要几百毫秒，此时直接 loadURL 会把品牌动画掐断在中途。
 * 剩余时长由启动页自己算（它知道动画从哪一刻开始播、当前播到一轮的哪个位置），
 * 主进程只负责等：等「当前这一轮」播完再切，因此无论加载快慢都不会看到半截动画。
 * 以下情况立刻放行（返回 0）：
 *   · 已经完整播完过至少一轮且正卡在轮次边界上
 *   · 系统开启"减少动效"、动画资源加载失败回退圆环 → 没有"一轮动画"可等
 */
function splashRemainingMs() {
  if (!mainWindow || mainWindow.isDestroyed()) return Promise.resolve(0);
  return mainWindow.webContents
    .executeJavaScript('window.__splashRemainingMs ? window.__splashRemainingMs() : 0', true)
    .then((ms) => (typeof ms === 'number' && ms > 0 ? ms : 0))
    .catch(() => 0);
}

/**
 * 等待后端服务就绪
 */
function waitForServer(port, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const startTime = Date.now();
    const check = () => {
      const socket = new net.Socket();
      socket.setTimeout(1000);
      socket.on('connect', () => {
        socket.destroy();
        resolve(true);
      });
      socket.on('error', () => {
        socket.destroy();
        if (Date.now() - startTime > timeout) {
          reject(new Error('服务器启动超时'));
        } else {
          setTimeout(check, 300);
        }
      });
      socket.on('timeout', () => {
        socket.destroy();
        if (Date.now() - startTime > timeout) {
          reject(new Error('服务器启动超时'));
        } else {
          setTimeout(check, 300);
        }
      });
      socket.connect(port, '127.0.0.1');
    };
    check();
  });
}

/**
 * 启动 Python 后端服务
 */
function startPythonServer(port) {
  return new Promise((resolve, reject) => {
    let serverExe;
    let serverArgs;
    let serverCwd;

    if (isDev) {
      // 开发模式：直接用 Python 运行
      // 按优先级查找 Python：环境变量 → 常见安装路径 → 系统 PATH
      const pythonCandidates = [
        process.env.PYTHON_PATH,
        'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python313\\python.exe',
        'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe',
        'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python311\\python.exe',
        'python',
        'python3',
      ].filter(Boolean);
      serverExe = pythonCandidates[0];
      serverArgs = ['server.py', '--port', String(port)];
      serverCwd = path.join(__dirname, '..', 'backend');
    } else {
      // 生产模式：运行 PyInstaller 打包后的可执行文件
      const serverDir = process.platform === 'win32'
        ? path.join(process.resourcesPath, 'moshen-server')
        : path.join(process.resourcesPath, 'moshen-server');
      serverExe = path.join(serverDir, 'moshen-server.exe');
      serverArgs = ['--port', String(port)];
      serverCwd = serverDir;
    }

    console.log(`启动后端服务: ${serverExe} ${serverArgs.join(' ')}`);

    pythonProcess = spawn(serverExe, serverArgs, {
      cwd: serverCwd,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    let stderrData = '';
    pythonProcess.stdout.on('data', (data) => {
      console.log(`[Python] ${data.toString().trim()}`);
    });
    pythonProcess.stderr.on('data', (data) => {
      const text = data.toString().trim();
      console.error(`[Python Error] ${text}`);
      stderrData += text;
    });

    pythonProcess.on('error', (err) => {
      console.error('Python 进程启动失败:', err.message);
      reject(err);
    });

    pythonProcess.on('exit', (code) => {
      console.log(`Python 进程退出，退出码: ${code}`);
      pythonProcess = null;
    });

    resolve();
  });
}

/**
 * 终止 Python 后端服务
 */
function killPythonServer() {
  console.log('正在停止后端服务...');
  try {
    if (process.platform === 'win32') {
      // 用 taskkill 终止所有 moshen-server 进程（含子进程树）
      execSync('taskkill /im moshen-server.exe /f /t', { windowsHide: true, stdio: 'ignore' });
    }
  } catch (e) {
    // taskkill 找不到进程会返回非零，忽略即可
  }
  if (pythonProcess) {
    try { pythonProcess.kill('SIGTERM'); } catch (e) {}
    pythonProcess = null;
  }
}

/**
 * 显示错误页面（后端启动失败时）
 * 配色与动画1 深色主题统一（§1.1）：底色 #0f1117、品牌紫 #7c5cfc、弱文本 #8b8fa3
 */
function showErrorPage(title, detail) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const html = `data:text/html;charset=utf-8,` + encodeURIComponent(`
    <!DOCTYPE html><html><head><meta charset="utf-8"><title>墨参 MoShen</title>
    <style>
    body{background:#0f1117;color:#e4e6eb;font-family:'Microsoft YaHei',sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
    .box{max-width:520px;text-align:center;padding:48px}
    .brand{color:#7c5cfc;font-size:15px;letter-spacing:2px;margin-bottom:28px;text-shadow:0 0 24px rgba(124,92,252,.35)}
    h1{color:#ff6b6b;margin:0 0 12px;font-size:22px}
    p{color:#8b8fa3;line-height:1.8;font-size:14px;margin:0}
    .hint{margin-top:24px;padding:16px;background:#181b24;border:1px solid #262a36;border-radius:8px;color:#a0a3b1;font-size:13px;text-align:left}
    .hint b{color:#E9E4FF}
    </style></head><body><div class="box">
    <div class="brand">墨参 MoShen</div>
    <h1>${title}</h1>
    <p>请截图反馈此页面，便于排查问题。</p>
    <div class="hint"><b>错误详情：</b><br>${detail}<br><br><b>排查步骤：</b><br>1. 确认 8765 端口未被占用<br>2. 关闭旧进程后重试<br>3. 按 F12 打开开发者工具查看控制台</div>
    </div></body></html>`);
  mainWindow.loadURL(html);
  mainWindow.show();
  mainWindow.focus();
}

/**
 * IPC：文件夹选择对话框
 */
ipcMain.handle('dialog:selectDirectory', async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: '选择文件夹',
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

/**
 * 创建主窗口
 */
async function createWindow() {
  // ★ 清一次本地缓存：绿色版是覆盖式同步，若不清理，Electron 会复用升级前的
  //   旧页面缓存（表现为左下角版本号还是老版本、新功能看不到）
  try {
    await session.defaultSession.clearCache();
    if (typeof session.defaultSession.clearCodeCaches === 'function') {
      await session.defaultSession.clearCodeCaches({});
    }
    console.log('已清理 HTTP / 代码缓存');
  } catch (e) {
    console.error('清理缓存失败:', e.message);
  }

  // 查找可用端口
  backendPort = await findFreePort();
  console.log(`使用端口: ${backendPort}`);

  // ★ 先创建并立即显示窗口，确保用户一定能看到界面
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: '墨参 MoShen · 个人写作 Agent',
    show: true,          // 创建即显示，不依赖任何事件
    autoHideMenuBar: true,
    backgroundColor: '#0f1117',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  // 先显示启动加载页（真实文件，可引用 frontend/assets/ink 下的动画资源）
  // ★ 原先是内联 data: URL 文档，data: 文档无法加载外部资源，
  //   因此改为 loadFile() 载入 electron/splash.html（见《加载与等待动画需求文档》§4.5 / §8.1）
  mainWindow
    .loadFile(path.join(__dirname, 'splash.html'))
    .catch((e) => console.error('启动页加载失败:', e.message));
  mainWindow.focus();

  // F12 打开/关闭开发者工具
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.key === 'F12') {
      mainWindow.webContents.toggleDevTools();
      event.preventDefault();
    }
  });

  // 外部链接在系统浏览器中打开
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // 窗口关闭
  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // ★ 窗口已显示后，再异步启动后端服务
  try {
    await startPythonServer(backendPort);
  } catch (e) {
    console.error('后端服务启动失败:', e.message);
    showErrorPage('后端服务启动失败', e.message);
    return;
  }

  // 等待服务就绪
  try {
    await waitForServer(backendPort);
    console.log('后端服务已就绪');
  } catch (e) {
    console.error('等待后端服务超时:', e.message);
    showErrorPage('后端服务启动超时', 'Python 后端在 15 秒内未响应。' + e.message);
    return;
  }

  // ★ 等当前这一轮启动动画播完再进主界面（剩余时长由启动页计算，
  //   见 electron/splash.html 的 __splashRemainingMs）
  const holdMs = await splashRemainingMs();
  if (holdMs > 0) {
    console.log(`等启动动画播完当前一轮，再等 ${Math.round(holdMs)}ms`);
    await new Promise((resolve) => setTimeout(resolve, holdMs));
  }

  // 后端就绪后，加载实际页面
  try {
    console.log('开始加载页面...');
    await mainWindow.loadURL(`http://127.0.0.1:${backendPort}`);
    console.log('页面加载完成');
    // 确保窗口可见
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      mainWindow.focus();
    }
  } catch (err) {
    console.error('页面加载失败:', err.message);
    showErrorPage('页面加载失败', err.message);
  }
}

// 应用准备就绪
app.whenReady().then(createWindow);

// 第二个实例尝试启动时，聚焦已有窗口
app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

// 所有窗口关闭时退出应用
app.on('window-all-closed', () => {
  killPythonServer();
  app.quit();
});

// 应用退出前清理
app.on('before-quit', () => {
  killPythonServer();
});

// 确保进程退出时彻底清理
process.on('exit', () => {
  killPythonServer();
});

// 防止 macOS 上激活应用时重新创建窗口
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
