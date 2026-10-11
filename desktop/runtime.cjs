const path = require("node:path");
const net = require("node:net");

const PORT = process.argv.includes("--smoke-test")
  ? Number(process.env.AIR780_SMOKE_PORT || 9012)
  : 9012;
if (!Number.isInteger(PORT) || PORT < 9000 || PORT > 9999)
  throw new Error("冒烟测试端口必须为 9xxx");
const ORIGIN = `http://127.0.0.1:${PORT}`;
function isConsoleUrl(url) {
  try {
    return new URL(url).origin === ORIGIN;
  } catch {
    return false;
  }
}
function backendOptions({
  packaged,
  resources,
  project,
  userData,
  instance,
  serialPort,
}) {
  const projectDir = packaged ? path.join(resources, "project") : project;
  const command = packaged
    ? path.join(resources, "backend-runtime", "air780-backend")
    : path.join(project, "backend", ".venv", "bin", "python");
  const args = [
    ...(packaged ? [] : [path.join(project, "backend", "desktop_entry.py")]),
    "--port",
    String(PORT),
    "--project-dir",
    projectDir,
    "--data-dir",
    path.join(userData, "data"),
    "--instance",
    instance,
  ];
  if (serialPort) args.push("--serial-port", serialPort);
  return { command, args, cwd: userData };
}
function assertFreePort(port = PORT) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", () =>
      reject(
        new Error(`${port} 端口已被占用，请关闭占用它的服务后重新打开应用。`),
      ),
    );
    server.listen(port, "127.0.0.1", () => server.close(resolve));
  });
}
function downloadPorts(ports) {
  // Electron's SerialPort converter exposes decimal strings, not hex strings.
  return ports.filter(
    (port) => Number(port.vendorId) === 0x17d1 && Number(port.productId) === 1,
  );
}
module.exports = {
  downloadPorts,
  PORT,
  ORIGIN,
  isConsoleUrl,
  backendOptions,
  assertFreePort,
};

function trayStatus(state) {
  const bridge = state?.bridge;
  const online = bridge?.ready === true && !!state?.device;
  const paused = bridge?.error === "browser_flashing";
  const mobile = online ? state.device.mobile : null;
  const csq = mobile?.csq;
  const signal =
    mobile?.sim_ready === true && Number.isInteger(csq) && csq >= 0 && csq <= 31
      ? `${csq}/31`
      : "未知";
  const connection = !state
    ? "服务未连接"
    : paused
      ? "桥接已暂停"
      : online
        ? "设备在线"
        : "设备离线";
  const labels = [
    `连接：${connection}`,
    `设备：${online ? state.device.project || "Air780EHV" : "—"}`,
    `版本：${online ? state.device.version || "—" : "—"}`,
    `SIM：${mobile ? (mobile.sim_ready ? "已就绪" : "未就绪") : "—"}`,
    `信号 CSQ：${online ? signal : "—"}`,
    `RSRP：${mobile?.sim_ready && Number.isFinite(mobile.rsrp) ? `${mobile.rsrp} dBm` : "—"}`,
    `串口：${bridge?.port || "未检测到"}`,
  ];
  return {
    title: online
      ? `Air780 · ${signal === "未知" ? "信号未知" : signal}`
      : `Air780 · ${paused ? "暂停" : "离线"}`,
    tooltip: labels.join("\n"),
    labels,
  };
}
module.exports.trayStatus = trayStatus;
