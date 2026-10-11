import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Progress, Segmented, Switch, Tag } from 'antd'
import { DownloadOutlined, UsbOutlined } from '@ant-design/icons'
import { controlFlashBridge, getFlashBridge, prepareFlash } from './connection'
import { serialApi, verifyPlan, WebFlasher } from './webFlash'
import type { FlashPlan, SerialPort } from './webFlash'

export default function HardwareFlash({ visible }: { visible: boolean }) {
  const supported = window.isSecureContext && !!serialApi()
  const [mode, setMode] = useState<'script' | 'full'>('script')
  const [plan, setPlan] = useState<FlashPlan | null>(null)
  const [paused, setPaused] = useState(false)
  const [bridgeState, setBridgeState] = useState<Awaited<ReturnType<typeof getFlashBridge>> | null>(
    null,
  )
  const [port, setPort] = useState<SerialPort | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<'idle' | 'success' | 'exception'>('idle')
  const [error, setError] = useState('')
  const [logs, setLogs] = useState<string[]>([])
  const abort = useRef<AbortController | null>(null)
  const log = (text: string) =>
    setLogs((items) => [...items.slice(-199), `${new Date().toLocaleTimeString('zh-CN')}  ${text}`])
  useEffect(() => {
    if (!visible) return
    let active = true
    const refresh = () =>
      getFlashBridge()
        .then((data) => {
          if (active) {
            setPaused(data.paused)
            setBridgeState(data)
          }
        })
        .catch((e) => {
          if (active) setError(e.message)
        })
    void refresh()
    const timer = window.setInterval(refresh, 2000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [visible])
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (abort.current) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', protect)
    return () => {
      window.removeEventListener('beforeunload', protect)
      abort.current?.abort()
    }
  }, [])
  const perform = async (task: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await task()
    } catch (e) {
      const text = e instanceof Error ? e.message : '操作失败'
      setError(text)
      log(text)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-6 max-[1000px]:grid-cols-1 max-[740px]:gap-4">
      <section className="min-w-0 rounded-2xl border border-[#dce5ed] bg-white p-6 max-[740px]:p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="m-0 text-base font-semibold">硬件刷写</h2>
          <Tag color={supported ? 'blue' : 'orange'}>
            {supported ? 'Chrome · Web Serial' : '浏览器不支持'}
          </Tag>
        </div>
        <p className="mt-2 text-sm text-[#6d8195]">Air780EHV · USB 下载口 · 浏览器直接传输</p>
        {!supported && (
          <Alert
            type="warning"
            showIcon
            title="请使用桌面 Chrome，在 localhost、127.0.0.1 或 HTTPS 下打开页面。"
          />
        )}
        <Alert
          type="info"
          showIcon
          title="刷写前备份设备文件与录音"
          description="仅脚本模式会覆盖脚本区；全量模式会更新底层固件。准备文件不会操作设备。请保持供电，刷写期间不要刷新或关闭页面。"
        />
        <div className="mt-4 rounded-xl bg-[#f2f7fc] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-sm font-semibold">设备串口连接</span>
            <Switch
              aria-label="设备串口连接"
              checked={!paused}
              checkedChildren="连接设备"
              unCheckedChildren="释放串口"
              disabled={busy || !bridgeState}
              loading={busy && !running}
              onChange={(checked) =>
                void perform(async () => {
                  const data = await controlFlashBridge(checked ? 'resume' : 'pause')
                  setPaused(data.paused)
                  log(checked ? '已启用设备连接，等待设备响应' : '已释放串口，可进行硬件刷写')
                })
              }
            />
          </div>
          <p className="mb-0 text-xs leading-6 text-[#6d8195]">
            {paused ? '串口已释放' : bridgeState?.ready ? '设备已连接' : '等待设备连接'}
            {bridgeState?.port && ` · ${bridgeState.port}`}
            <br />
            开启后连接设备；Mac 应用会先释放同一串口的开发控制台桥接。刷写前请关闭，刷写后重新开启。
          </p>
          {bridgeState?.error && !paused && (
            <Alert
              className="mt-3!"
              type="warning"
              showIcon
              title={
                bridgeState.error.includes('exclusively lock')
                  ? '串口被占用：点击下方重新连接，或关闭其他串口工具。'
                  : '设备尚未连接，请检查 USB 连接与 BOOT 状态。'
              }
            />
          )}
          <Button
            className="mt-3"
            disabled={busy || paused}
            onClick={() =>
              void perform(async () => {
                await controlFlashBridge('resume')
                log('已请求重新连接；开发控制台占用将先释放')
              })
            }
          >
            重新连接 / 释放开发占用
          </Button>
        </div>
        <div className="mt-6 flex flex-col gap-4">
          <div>
            <h3 className="m-0 mb-2 text-sm font-semibold">1 · 准备刷写文件</h3>
            <Segmented
              disabled={busy}
              value={mode}
              options={[
                { label: '仅更新脚本', value: 'script' },
                { label: '底层固件＋脚本', value: 'full' },
              ]}
              onChange={(value) => {
                setMode(value as 'script' | 'full')
                setPlan(null)
                setConfirmed(false)
              }}
            />
            <p className="text-xs text-[#6d8195]">
              使用本机 V2052 Air780EHV_13 官方固件，编译当前 lua/ 项目。
            </p>
            <Button
              disabled={busy}
              loading={busy && !running}
              icon={<DownloadOutlined />}
              onClick={() =>
                void perform(async () => {
                  setPlan(null)
                  setConfirmed(false)
                  setResult('idle')
                  setProgress(0)
                  const prepared = await prepareFlash(mode)
                  await verifyPlan(prepared)
                  setPlan(prepared)
                  log('固件、脚本与 SHA-256 校验已完成')
                })
              }
            >
              准备并校验文件
            </Button>
            {plan && (
              <div className="mt-3 rounded-xl bg-[#f2f7fc] p-3 text-xs">
                <p className="m-0 break-all font-mono">{plan.firmware}</p>
                <p className="mt-1 mb-2 text-[#6d8195]">
                  {plan.chip} · {plan.mode === 'script' ? '脚本更新' : '全量刷写'}
                </p>
                {plan.entries.map((image) => (
                  <div
                    key={image.name}
                    className="flex flex-wrap justify-between gap-2 py-1 font-mono"
                  >
                    <span>
                      {image.name} @ 0x{image.address.toString(16).toUpperCase()}
                    </span>
                    <span>{(image.size / 1024).toFixed(1)} KB</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="border-t border-[#e6eff8] pt-4">
            <h3 className="m-0 mb-2 text-sm font-semibold">2 · 释放串口并进入 BOOT 模式</h3>
            <p className="text-xs leading-6 text-[#6d8195]">
              先暂停桥接，关闭其他串口工具。按住 BOOT 后复位或重新上电，再松开
              BOOT。选择重新枚举的下载口（通常后缀 11），不要选择日志口或应用口。
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={busy || paused}
                onClick={() =>
                  void perform(async () => {
                    const data = await controlFlashBridge('pause')
                    setPaused(data.paused)
                    log('后台串口已释放')
                  })
                }
              >
                暂停后台桥接
              </Button>
              <Button
                disabled={busy || !paused || !supported}
                icon={<UsbOutlined />}
                onClick={() =>
                  void perform(async () => {
                    // requestPort must occur directly within the click's user activation.
                    const selected = await serialApi()!.requestPort({
                      filters: [{ usbVendorId: 0x17d1, usbProductId: 1 }],
                    })
                    setPort(selected)
                    setConfirmed(false)
                    log('已授权 USB 下载口，尚未打开或写入')
                  })
                }
              >
                {port ? '重新选择下载口' : '选择下载串口'}
              </Button>
            </div>
            <p className="text-xs text-[#6d8195]">
              桥接：{paused ? '已暂停' : '运行中'} · 下载口：{port ? '已授权' : '未选择'}
            </p>
          </div>
          <div className="border-t border-[#e6eff8] pt-4">
            <h3 className="m-0 mb-3 text-sm font-semibold">3 · 开始刷写</h3>
            <Checkbox
              disabled={busy}
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            >
              已确认型号、刷写分区和备份，设备已进入 BOOT 模式
            </Checkbox>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                type="primary"
                disabled={!supported || !plan || !port || !paused || !confirmed || busy}
                onClick={() =>
                  void perform(async () => {
                    const bridge = await getFlashBridge()
                    if (!bridge.paused) {
                      setPaused(false)
                      throw new Error('后台桥接已恢复，请重新暂停后刷写')
                    }
                    setRunning(true)
                    setResult('idle')
                    setProgress(0)
                    setLogs([])
                    const controller = new AbortController()
                    abort.current = controller
                    try {
                      await new WebFlasher(port!, controller.signal, (percent, text) => {
                        setProgress(Math.round(percent))
                        log(text)
                      }).run(plan!)
                      setResult('success')
                    } catch (e) {
                      setResult('exception')
                      throw e
                    } finally {
                      abort.current = null
                      setRunning(false)
                      setPort(null)
                      setConfirmed(false)
                    }
                  })
                }
              >
                开始刷写
              </Button>
              {running && (
                <Button danger onClick={() => abort.current?.abort()}>
                  停止刷写
                </Button>
              )}
              <Button
                disabled={busy || !paused}
                onClick={() =>
                  void perform(async () => {
                    const data = await controlFlashBridge('resume')
                    setPaused(data.paused)
                    setPort(null)
                    log('已恢复后台桥接，等待设备正常上电连接')
                  })
                }
              >
                恢复后台桥接
              </Button>
            </div>
          </div>
        </div>
      </section>
      <section className="min-w-0 rounded-2xl border border-[#dce5ed] bg-white p-6 max-[740px]:p-5">
        <h2 className="m-0 mb-4 text-base font-semibold">刷写进度与日志</h2>
        <Progress
          percent={progress}
          status={
            result === 'exception'
              ? 'exception'
              : result === 'success'
                ? 'success'
                : running
                  ? 'active'
                  : 'normal'
          }
        />
        {error && <Alert className="mt-4!" type="error" showIcon title={error} />}
        {result === 'success' && (
          <Alert
            className="mt-4!"
            type="success"
            showIcon
            title="分区刷写与重启已确认"
            description="松开 BOOT，正常上电后点击恢复后台桥接，并在设备总览确认版本。"
          />
        )}
        <pre
          role="log"
          aria-label="刷写日志"
          className="mt-4 h-80 overflow-auto rounded-xl bg-[#102f48] p-4 font-mono text-xs leading-6 whitespace-pre-wrap wrap-anywhere text-[#d9eaf7]"
        >
          {logs.length ? logs.join('\n') : '等待准备文件与选择下载串口。'}
        </pre>
        <p className="text-xs leading-6 text-[#6d8195]">
          停止、超时或 USB 断开后，结果可能未知。桥接保持暂停；请重新进入 BOOT
          模式再手动刷写。菜单切换不会中断任务。
        </p>
      </section>
    </div>
  )
}
