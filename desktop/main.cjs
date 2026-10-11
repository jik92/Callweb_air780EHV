const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  nativeImage,
  dialog,
  shell,
  session,
  systemPreferences,
} = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const {
  ORIGIN,
  isConsoleUrl,
  backendOptions,
  assertFreePort,
  downloadPorts,
  trayStatus,
} = require("./runtime.cjs");

const NAME = "智能语音客服";
let window, tray, backend, logStream;
let trayTimer, trayMenu, lastTrayStatus;
let trayRefreshing = false;
let quitting = false,
  backendStopped = false,
  ready = false;
const smoke = process.argv.includes("--smoke-test");
const project = path.resolve(__dirname, "..");
app.setName("Air780 Console");
if (smoke) {
  const temp =
    process.env.AIR780_SMOKE_DATA ||
    fs.mkdtempSync(path.join(require("node:os").tmpdir(), "air780-mac-smoke-"));
  app.setPath("userData", path.resolve(temp));
}
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => showWindow());
  app
    .whenReady()
    .then(start)
    .catch(async (error) => {
      if (smoke) {
        quitting = true;
        console.error(error);
        await stopBackend();
        app.exit(1);
        return;
      }
      dialog.showErrorBox(
        "控制台启动失败",
        `${error.message}\n应用日志：${app.getPath("userData")}/logs/backend.log`,
      );
      app.quit();
    });
}
function showWindow() {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}
async function startBackend() {
  await assertFreePort();
  const userData = app.getPath("userData");
  fs.mkdirSync(path.join(userData, "logs"), { recursive: true });
  logStream = fs.createWriteStream(path.join(userData, "logs/backend.log"), {
    flags: "a",
    mode: 0o600,
  });
  const instance = randomUUID();
  const config = backendOptions({
    packaged: app.isPackaged,
    resources: process.resourcesPath,
    project,
    userData,
    instance,
    serialPort: smoke
      ? "/dev/air780-desktop-smoke"
      : process.env.AIR780_SERIAL_PORT,
  });
  backend = spawn(config.command, config.args, {
    cwd: config.cwd,
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });
  backend.stdout.pipe(logStream, { end: false });
  backend.stderr.pipe(logStream, { end: false });
  let failure = null;
  backend.on("error", (error) => {
    failure = error;
  });
  backend.on("exit", (code, signal) => {
    backendStopped = true;
    if (!quitting && ready) {
      ready = false;
      dialog.showErrorBox(
        "后台服务已停止",
        `退出状态：${code ?? signal}。请退出并重新打开应用；不会自动重发设备命令。`,
      );
    }
  });
  for (let attempt = 0; attempt < 150; attempt++) {
    if (failure) throw failure;
    if (backendStopped) throw new Error("后台启动失败，请查看 backend.log");
    try {
      const response = await fetch(`${ORIGIN}/api/health`, {
        signal: AbortSignal.timeout(500),
      });
      const data = await response.json();
      if (response.ok && data.desktop_instance === instance) {
        ready = true;
        return;
      }
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("后台服务启动超时");
}
async function stopBackend() {
  if (!backend || backendStopped) {
    logStream?.end();
    return;
  }
  await new Promise((resolve) => {
    const child = backend;
    let timer;
    const done = () => {
      clearTimeout(timer);
      resolve();
    };
    child.once("exit", done);
    // EOF tells the embedded service to finish its lifespan and release serial.
    child.stdin.end();
    timer = setTimeout(() => {
      child.kill("SIGKILL");
      resolve();
    }, 6000);
  });
  logStream?.end();
}
function configurePermissions() {
  const ses = session.defaultSession;
  ses.setPermissionCheckHandler(
    (contents, permission, origin) =>
      !!contents &&
      isConsoleUrl(contents.getURL()) &&
      isConsoleUrl(origin) &&
      ["serial", "media", "clipboard-sanitized-write"].includes(permission),
  );
  ses.setPermissionRequestHandler(
    async (contents, permission, callback, details) => {
      if (
        !isConsoleUrl(contents.getURL()) ||
        !isConsoleUrl(details.requestingUrl || contents.getURL())
      ) {
        callback(false);
        return;
      }
      if (
        permission === "media" &&
        (!details.mediaTypes ||
          details.mediaTypes.every((type) => type === "audio"))
      ) {
        try {
          callback(await systemPreferences.askForMediaAccess("microphone"));
        } catch {
          callback(false);
        }
      } else
        callback(
          permission === "serial" || permission === "clipboard-sanitized-write",
        );
    },
  );
  // Electron retains grants made by the manual chooser for this session.
  // Do not install a default handler that silently authorizes devices.
  ses.on("select-serial-port", async (event, ports, contents, callback) => {
    event.preventDefault();
    if (!isConsoleUrl(contents.getURL()) || contents !== window?.webContents) {
      callback("");
      return;
    }
    const downloads = downloadPorts(ports);
    if (!downloads.length) {
      callback("");
      await dialog.showMessageBox(window, {
        type: "info",
        message: "没有发现 USB 下载串口",
        detail:
          "先暂停后台桥接，按住 BOOT 后复位或重新上电，再重新点击选择下载串口。",
      });
      return;
    }
    try {
      const result = await dialog.showMessageBox(window, {
        type: "question",
        message: "选择 Air780EHV USB 下载串口",
        detail:
          "仅授权所选端口。点击页面「开始刷写」后才写入；设备必须处于 BOOT 模式。",
        buttons: [
          "取消",
          ...downloads.map(
            (port) => `${port.displayName || port.portName} · ${port.portName}`,
          ),
        ],
        cancelId: 0,
        defaultId: 0,
      });
      const port = downloads[result.response - 1];
      callback(port?.portId || "");
    } catch {
      callback("");
    }
  });
}
function createWindow() {
  window = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 360,
    minHeight: 560,
    title: NAME,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  window.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      window.hide();
    }
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (!isConsoleUrl(url)) {
      event.preventDefault();
      openExternal(url);
    }
  });
  window.once("ready-to-show", () => {
    if (!smoke) showWindow();
  });
  return window.loadURL(ORIGIN);
}
function openExternal(url) {
  try {
    if (["https:", "http:"].includes(new URL(url).protocol))
      void shell.openExternal(url);
  } catch {}
}
function updateTray(state) {
  if (!tray || tray.isDestroyed() || quitting) return;
  const status = trayStatus(state);
  const signature = JSON.stringify(status);
  if (signature === lastTrayStatus) return;
  lastTrayStatus = signature;
  status.labels.forEach((label, index) => {
    trayMenu.getMenuItemById(`device-${index}`).label = label;
  });
  tray.setTitle(status.title);
  tray.setToolTip(status.tooltip);
  tray.setContextMenu(trayMenu);
}
async function refreshTray() {
  if (trayRefreshing || quitting) return;
  trayRefreshing = true;
  try {
    if (!ready) throw new Error("backend unavailable");
    const response = await fetch(`${ORIGIN}/api/state`, {
      signal: AbortSignal.timeout(1500),
    });
    if (!response.ok) throw new Error("state unavailable");
    updateTray(await response.json());
  } catch {
    updateTray(null);
  } finally {
    trayRefreshing = false;
  }
}
async function start() {
  configurePermissions();
  const assetRoot = app.isPackaged
    ? process.resourcesPath
    : path.join(project, "build/mac-assets");
  const icon = nativeImage.createFromPath(
    path.join(assetRoot, "trayTemplate.png"),
  );
  if (icon.isEmpty()) throw new Error("菜单栏图标缺失，请先执行一键编译命令");
  icon.setTemplateImage(true);
  tray = new Tray(icon);
  tray.setToolTip(`${NAME} · Air780EHV`);
  trayMenu = Menu.buildFromTemplate([
    ...trayStatus(null).labels.map((label, index) => ({
      id: `device-${index}`,
      label,
      enabled: false,
    })),
    { type: "separator" },
    { label: "打开控制台", click: showWindow },
    {
      label: "在 Chrome 中打开",
      click: () => {
        // Explicit Chrome launcher without shell interpolation.
        const child = spawn("/usr/bin/open", ["-a", "Google Chrome", ORIGIN], {
          stdio: "ignore",
        });
        child.on("error", (error) =>
          dialog.showErrorBox("无法打开 Chrome", error.message),
        );
        child.on("exit", (code) => {
          if (code)
            dialog.showErrorBox("无法打开 Chrome", "请先安装 Google Chrome。");
        });
      },
    },
    {
      label: "查看数据目录",
      click: () => {
        void shell.openPath(path.join(app.getPath("userData"), "data"));
      },
    },
    {
      label: "查看日志",
      click: () => {
        void shell.openPath(path.join(app.getPath("userData"), "logs"));
      },
    },
    { type: "separator" },
    { label: "退出", click: () => app.quit() },
  ]);
  tray.setContextMenu(trayMenu);
  updateTray(null);
  tray.on("double-click", showWindow);
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: NAME,
        submenu: [
          { role: "about" },
          { type: "separator" },
          { role: "hide" },
          { role: "hideOthers" },
          { role: "unhide" },
          { type: "separator" },
          { label: "退出", accelerator: "Command+Q", click: () => app.quit() },
        ],
      },
      { role: "editMenu" },
      { role: "viewMenu" },
      { role: "windowMenu" },
    ]),
  );
  await startBackend();
  await refreshTray();
  trayTimer = setInterval(() => void refreshTray(), 2000);
  await createWindow();
  app.on("activate", showWindow);
  if (smoke) {
    const health = await (await fetch(`${ORIGIN}/api/health`)).json();
    const result = await window.webContents.executeJavaScript(
      `({ title:document.title, serial:'serial' in navigator, secure:window.isSecureContext, text:document.body.innerText })`,
    );
    if (
      !health.ok ||
      !result.serial ||
      !result.secure ||
      !result.text.includes("硬件刷写")
    )
      throw new Error("应用冒烟检查失败");
    // Read-only device state and file preparation: smoke mode owns a dummy serial.
    const state = await (await fetch(`${ORIGIN}/api/state`)).json();
    if (state.bridge.ready) throw new Error("冒烟测试不能连接真实设备");
    await refreshTray();
    const expectedTray = trayStatus(state);
    if (
      tray.getTitle() !== expectedTray.title ||
      trayMenu.getMenuItemById("device-6").label !== expectedTray.labels[6]
    )
      throw new Error("菜单栏设备信息检查失败");
    const prepared = await fetch(`${ORIGIN}/api/flash/prepare`, {
      method: "POST",
      headers: { Origin: ORIGIN, "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "script" }),
    });
    const plan = await prepared.json();
    if (
      !prepared.ok ||
      plan.agent?.size !== 40696 ||
      plan.entries?.[0]?.name !== "script"
    )
      throw new Error("包内脚本编译或固件资源检查失败");
    await new Promise((resolve) => setTimeout(resolve, 300));
    const wsConnected = await window.webContents.executeJavaScript(
      `document.body.innerText.includes('服务已连接')`,
    );
    if (!wsConnected) throw new Error("封装页面 WebSocket 未连接");
    console.log(
      JSON.stringify({
        packaged: app.isPackaged,
        arm64: process.arch === "arm64",
        tray: !tray.isDestroyed(),
        trayStatus: true,
        title: result.title,
        webSerial: result.serial,
        secure: result.secure,
        backend: health.ok,
        scriptCompile: true,
        websocket: wsConnected,
      }),
    );
    app.quit();
  }
}
app.on("before-quit", (event) => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  clearInterval(trayTimer);
  void stopBackend().finally(() => {
    tray?.destroy();
    app.quit();
  });
});
app.on("window-all-closed", () => {});
