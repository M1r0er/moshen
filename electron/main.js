/**
 * 墨参 MoShen · Electron 主进程
 * 负责启动 Python 后端服务、创建桌面窗口、管理应用生命周期
 */
const { app, BrowserWindow, shell, globalShortcut } = require('electron');
const path = require('path');
const net = require('net');
const { spawn } = require('child_process');

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
  if (pythonProcess) {
    console.log('正在停止后端服务...');
    try {
      if (process.platform === 'win32') {
        // Windows 下用 taskkill 强制终止进程树
        spawn('taskkill', ['/pid', pythonProcess.pid, '/f', '/t'], {
          windowsHide: true,
        });
      } else {
        pythonProcess.kill('SIGTERM');
      }
    } catch (e) {
      console.error('终止 Python 进程失败:', e.message);
    }
    pythonProcess = null;
  }
}

/**
 * 创建主窗口
 */
async function createWindow() {
  // 查找可用端口
  backendPort = await findFreePort();
  console.log(`使用端口: ${backendPort}`);

  // 启动后端服务
  try {
    await startPythonServer(backendPort);
  } catch (e) {
    console.error('后端服务启动失败:', e.message);
    return;
  }

  // 等待服务就绪
  try {
    await waitForServer(backendPort);
    console.log('后端服务已就绪');
  } catch (e) {
    console.error('等待后端服务超时:', e.message);
    return;
  }

  // 创建浏览器窗口
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: '墨参 MoShen · 小说写作助手',
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // 加载后端服务地址（失败不阻塞窗口显示）
  try {
    await mainWindow.loadURL(`http://127.0.0.1:${backendPort}`);
  } catch (err) {
    console.error('页面加载失败:', err.message);
  }

  // 立即显示窗口（不依赖 ready-to-show，避免窗口永不显示）
  mainWindow.show();
  mainWindow.focus();

  // 兜底：若页面渲染较慢，1 秒后强制显示
  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) {
      mainWindow.show();
    }
  }, 1000);

  // F12 打开/关闭开发者工具（生产模式也可用）
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
}

// 应用准备就绪
app.whenReady().then(createWindow);

// 所有窗口关闭时退出应用
app.on('window-all-closed', () => {
  killPythonServer();
  app.quit();
});

// 应用退出前清理
app.on('before-quit', () => {
  killPythonServer();
});

// 防止 macOS 上激活应用时重新创建窗口
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
