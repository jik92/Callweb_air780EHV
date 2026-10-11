const { test } = require("node:test");
const assert = require("node:assert/strict");
const net = require("node:net");
const {
  ORIGIN,
  isConsoleUrl,
  backendOptions,
  assertFreePort,
  downloadPorts,
} = require("./runtime.cjs");
test("window allows only the owned local origin", () => {
  assert.ok(isConsoleUrl(`${ORIGIN}/api/state`));
  for (const url of [
    "http://127.0.0.1:9010",
    "http://127.0.0.1:9012.attacker.test",
    "https://example.com",
    "file:///tmp/test",
    "broken",
  ])
    assert.equal(isConsoleUrl(url), false);
});
test("packaged runtime uses shipped executable/resources and separate writable data", () => {
  const options = backendOptions({
    packaged: true,
    resources: "/Applications/Air780 Console.app/Contents/Resources",
    project: "/unused",
    userData: "/Users/test/Library/Application Support/Air780 Console",
    instance: "test",
    serialPort: "/dev/mock",
  });
  assert.equal(
    options.command,
    "/Applications/Air780 Console.app/Contents/Resources/backend-runtime/air780-backend",
  );
  assert.equal(options.args[0], "--port");
  assert.equal(options.args[1], "9012");
  assert.ok(
    options.args.includes(
      "/Applications/Air780 Console.app/Contents/Resources/project",
    ),
  );
  assert.ok(
    options.args.includes(
      "/Users/test/Library/Application Support/Air780 Console/data",
    ),
  );
  assert.equal(options.args.at(-1), "/dev/mock");
});
test("occupied ports are reported without terminating other services", async () => {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    await assert.rejects(assertFreePort(server.address().port), /端口已被占用/);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("chooser only lists EHV USB download ports", () => {
  const download = { portId: "boot", vendorId: "6097", productId: "1" };
  assert.deepEqual(
    downloadPorts([
      download,
      { portId: "application", vendorId: "6609", productId: "1" },
      { portId: "bluetooth" },
    ]),
    [download],
  );
});

test("tray shows live SIM signal and full port, hides stale device data", () => {
  const { trayStatus } = require("./runtime.cjs");
  const state = {
    bridge: { ready: true, port: "/dev/cu.mock17" },
    device: {
      project: "Air780EHV",
      version: "test-version",
      mobile: { sim_ready: true, csq: 24, rsrp: -89 },
    },
  };
  const online = trayStatus(state);
  assert.equal(online.title, "Air780 · 24/31");
  assert.ok(online.labels.includes("串口：/dev/cu.mock17"));
  assert.ok(online.labels.includes("RSRP：-89 dBm"));
  state.device.mobile.csq = 99;
  assert.equal(trayStatus(state).title, "Air780 · 信号未知");
  state.device.mobile.sim_ready = false;
  state.device.mobile.csq = 24;
  assert.ok(trayStatus(state).labels.includes("信号 CSQ：未知"));
  state.bridge.ready = false;
  const offline = trayStatus(state);
  assert.equal(offline.title, "Air780 · 离线");
  assert.ok(offline.labels.includes("版本：—"));
  assert.ok(offline.labels.includes("信号 CSQ：—"));
  state.bridge.error = "browser_flashing";
  assert.equal(trayStatus(state).title, "Air780 · 暂停");
  assert.ok(trayStatus(null).labels.includes("连接：服务未连接"));
});
