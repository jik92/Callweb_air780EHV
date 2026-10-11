import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert,
  App,
  Badge,
  Button,
  Empty,
  Form,
  Input,
  Segmented,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  CopyOutlined,
  MessageOutlined,
  ReloadOutlined,
  SendOutlined,
  UsbOutlined,
} from '@ant-design/icons'
import { connection, stateKey } from './connection'
import type { Sms, Snapshot } from './types'

const signalHeights = [
  'h-3',
  'h-[17px]',
  'h-[22px]',
  'h-[27px]',
  'h-8',
  'h-[37px]',
  'h-[42px]',
  'h-[47px]',
]

const time = (timestamp: number | null) =>
  timestamp ? new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'
const states: Record<Sms['state'], { label: string; color: string }> = {
  received: { label: '已接收', color: 'blue' },
  queued: { label: '等待发送', color: 'gold' },
  sending: { label: '发送中', color: 'gold' },
  submitted: { label: '已提交短信中心', color: 'green' },
  failed: { label: '发送失败', color: 'red' },
  unknown: { label: '结果未知', color: 'orange' },
}
const errors: Record<string, string> = {
  sms_not_ready: '短信功能尚未就绪',
  busy: '设备正在处理其他短信',
  serial_disconnected: '发送期间串口断开',
  service_restarted: '服务重启，未取得发送结果',
  result_timeout: '未在 90 秒内取得发送结果',
}

function Info({
  label,
  value,
  copy = false,
}: {
  label: string
  value: string | number | undefined
  copy?: boolean
}) {
  const { message } = App.useApp()
  return (
    <div className="flex min-h-[37px] items-center justify-between gap-2.5 text-xs [&>span:first-child]:shrink-0 [&>span:first-child]:text-[#6d8195] [&>span:last-child]:text-right [&>span:last-child]:wrap-anywhere">
      <span>{label}</span>
      <span className={copy ? 'font-mono tracking-[-0.3px] tabular-nums' : ''}>
        {value ?? '—'}
        {copy && value && (
          <Button
            type="text"
            size="small"
            icon={<CopyOutlined />}
            aria-label={`复制${label}`}
            onClick={async () => {
              await navigator.clipboard.writeText(String(value))
              message.success(`${label} 已复制`)
            }}
          />
        )}
      </span>
    </div>
  )
}
export default function Console() {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [filter, setFilter] = useState('全部')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const content = Form.useWatch('content', form) ?? ''
  const bytes = new TextEncoder().encode(content).length
  const wsStatus = useSyncExternalStore(connection.subscribe, connection.getStatus)
  const state = useQuery<Snapshot>({
    queryKey: stateKey,
    queryFn: async () => {
      const response = await fetch('/api/state')
      if (!response.ok) throw new Error('无法读取服务状态')
      return response.json()
    },
    staleTime: Infinity,
  })
  useEffect(() => {
    connection.start()
    return () => connection.stop()
  }, [])
  const data = state.data
  const mobile = data?.device?.mobile
  const ready = wsStatus === 'connected' && data?.bridge.ready === true
  const busy = data?.messages.some((item) => ['queued', 'sending'].includes(item.state)) ?? false
  const refresh = useMutation({
    mutationFn: async () => {
      await connection.command('get_status')
      await connection.command('get_inbox')
    },
    onError: (error) => message.error(error.message),
  })
  const send = useMutation({
    mutationFn: (values: { to: string; content: string }) => connection.command('send_sms', values),
    onSuccess: () => {
      form.setFieldValue('content', '')
      message.success('发送请求已交给设备，请查看发送状态')
    },
    onError: (error) => message.error(error.message),
  })
  const messages = useMemo(
    () =>
      (data?.messages ?? []).filter(
        (item) =>
          (filter === '全部' ||
            item.direction === (filter === '收件箱' ? 'incoming' : 'outgoing')) &&
          `${item.phone} ${item.content}`.includes(search),
      ),
    [data, filter, search],
  )
  const incoming = data?.messages.filter((item) => item.direction === 'incoming').length ?? 0
  const outgoing = data?.messages.filter((item) => item.direction === 'outgoing').length ?? 0
  return (
    <div className="mx-auto min-h-screen max-w-[1600px] bg-[#eef3f8] text-[#163b5c] [&_:focus-visible]:outline-2 [&_:focus-visible]:outline-[#1766c2] [&_:focus-visible]:outline-offset-3 motion-reduce:[&_*]:transition-none! motion-reduce:[&_*]:animate-none!">
      <header className="flex h-[82px] items-center justify-between border-b border-[#dce5ed] bg-white px-[38px] max-[1000px]:px-6 max-[740px]:h-[70px] max-[740px]:px-4">
        <a
          className="flex items-center gap-3 text-[#163b5c]! no-underline [&_strong]:text-[23px] [&_strong]:tracking-[1px] [&_strong_span]:text-[#1766c2]"
          href="/"
        >
          <span className="grid size-10 place-items-center rounded-[11px] bg-[#1766c2] text-[23px] text-white">
            <UsbOutlined />
          </span>
          <strong>
            AIR<span>780</span>
          </strong>
          <span className="ml-1 border-l border-[#dce5ed] pl-4 text-sm text-[#61758a] max-[740px]:hidden">
            设备控制台
          </span>
        </a>
        <div className="flex items-center gap-[18px] max-[740px]:gap-[5px]">
          <Tag variant="filled">本地开发</Tag>
          <Badge
            status={wsStatus === 'connected' ? 'success' : 'processing'}
            text={wsStatus === 'connected' ? '服务已连接' : '服务重连中'}
          />
        </div>
      </header>
      <main className="grid grid-cols-[350px_minmax(0,1fr)] gap-6 px-[38px] pt-[30px] pb-5 min-[1450px]:grid-cols-[380px_minmax(0,1fr)] max-[1000px]:grid-cols-[310px_minmax(0,1fr)] max-[1000px]:gap-[18px] max-[1000px]:p-6 max-[740px]:grid-cols-1 max-[740px]:p-4">
        <aside className="flex min-w-0 flex-col gap-5 max-[740px]:gap-4">
          <section className="p-6 max-[740px]:p-5 rounded-2xl border border-[#dce5ed] bg-white shadow-[0_3px_14px_#163b5c03]">
            <div className="mb-2.5 text-xs tracking-[0.08em] text-[#6d8195]">当前设备</div>
            <div className="flex items-center justify-between [&_h1]:m-0 [&_h1]:text-[28px] [&_h1]:font-[650] [&_h1]:tracking-[-0.6px]">
              <h1>Air780EHV</h1>
              <Tag color={ready ? 'green' : 'default'}>{ready ? '在线' : '离线'}</Tag>
            </div>
            <p className="mt-2 mb-[22px] text-[13px] text-[#6d8195]">USB 连接 · 本机串口桥接</p>
            <div className="mb-5 flex items-center gap-[22px] rounded-xl border border-[#e6eff8] bg-[#f2f7fc] p-[18px] max-[740px]:mb-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-[26px] [&_strong]:font-[550] [&_small]:text-[13px] [&_small]:text-[#8a9aac] [&_div>span]:text-xs [&_div>span]:text-[#61758a]">
              <div
                className="flex h-12 items-end gap-[5px]"
                aria-label={`信号强度 ${mobile?.csq ?? 0}`}
              >
                {Array.from({ length: 8 }, (_, i) => (
                  <span
                    key={i}
                    className={`w-[7px] rounded-[3px] ${signalHeights[i]} ${mobile && mobile.csq !== 99 && i < Math.ceil(mobile.csq / 4) ? 'bg-[#1766c2]' : 'bg-[#d5e1ee]'}`}
                  />
                ))}
              </div>
              <div>
                <strong>
                  {mobile?.csq === 99 ? '—' : (mobile?.csq ?? '—')}
                  <small> / 31</small>
                </strong>
                <span>信号强度 CSQ</span>
              </div>
            </div>
            <Info label="串口" value={data?.bridge.port?.replace('/dev/', '')} />
            <Info label="应用版本" value={data?.device?.version} />
            <Info label="最近更新" value={time(data?.bridge.last_seen ?? null)} />
            <Button
              className="mt-4 h-[38px]!"
              block
              icon={<ReloadOutlined />}
              loading={refresh.isPending}
              disabled={!ready}
              onClick={() => refresh.mutate()}
            >
              刷新设备信息
            </Button>
          </section>
          <section className="rounded-2xl border border-[#dce5ed] bg-white shadow-[0_3px_14px_#163b5c03] p-6 max-[740px]:p-5">
            <div className="flex items-center justify-between gap-3 [&_h2]:m-0 [&_h2]:text-base [&_h2]:font-semibold [&_.ant-badge-status-text]:text-xs!">
              <h2>SIM 卡信息</h2>
              <Badge
                status={mobile?.sim_ready && ready ? 'success' : 'default'}
                text={mobile?.sim_ready && ready ? '已就绪' : '未就绪'}
              />
            </div>
            <div className="mt-[18px] mb-3 border-y border-[#e9eff5] py-[19px] [&>span]:block [&>span]:text-xs [&>span]:text-[#6d8195] [&_strong]:my-2 [&_strong]:block [&_strong]:text-[22px] [&_strong]:font-[550] [&_p]:m-0 [&_p]:text-xs [&_p]:text-[#8a9aac]">
              <span>本机号码</span>
              <strong>{mobile?.number || '暂未提供'}</strong>
              {!mobile?.number && <p>当前 SIM 未返回本机号码</p>}
            </div>
            <Info label="ICCID" value={mobile?.iccid} copy />
            <Info label="IMSI" value={mobile?.imsi} copy />
            <Info label="卡槽" value={mobile ? `SIM ${mobile.sim_slot}` : undefined} />
            <Info
              label="网络注册"
              value={
                mobile
                  ? mobile.net_status === 1
                    ? '已注册'
                    : `状态 ${mobile.net_status}`
                  : undefined
              }
            />
            <div className="mt-[17px] grid grid-cols-2 gap-3.5 border-t border-[#e9eff5] pt-[17px] [&_span]:block [&_span]:text-[11px] [&_span]:text-[#6d8195] [&_strong]:mt-1.5 [&_strong]:block [&_strong]:font-mono [&_strong]:text-[19px] [&_small]:text-[11px] [&_small]:font-normal [&_small]:text-[#8a9aac]">
              <div>
                <span>RSRP</span>
                <strong>
                  {mobile?.rsrp ?? '—'}
                  <small> dBm</small>
                </strong>
              </div>
              <div>
                <span>SNR</span>
                <strong>
                  {mobile?.snr ?? '—'}
                  <small> dB</small>
                </strong>
              </div>
            </div>
          </section>
          <div className="flex items-start gap-[9px] px-[9px] text-xs leading-[1.8] text-[#6d8195] max-[740px]:hidden [&>.anticon]:mt-[5px] [&>.anticon]:text-[#197d68]">
            <CheckCircleOutlined />
            <span>短信保存在本机，重新打开页面可继续查看。</span>
          </div>
        </aside>
        <div className="flex min-w-0 flex-col gap-5 max-[740px]:gap-4">
          <section className="rounded-2xl border border-[#dce5ed] bg-white shadow-[0_3px_14px_#163b5c03] flex min-h-[420px] flex-1 flex-col overflow-hidden [&>.ant-alert]:mx-[26px] [&>.ant-alert]:mt-4">
            <div className="flex items-center justify-between px-[26px] pt-6 pb-5 max-[740px]:p-5 [&_h2]:m-0 [&_h2]:text-2xl [&_h2]:tracking-[-0.4px] [&_h2>span]:ml-[5px] [&_h2>span]:rounded-md [&_h2>span]:bg-[#edf3fa] [&_h2>span]:px-2 [&_h2>span]:py-[3px] [&_h2>span]:align-middle [&_h2>span]:text-[13px] [&_h2>span]:text-[#1766c2]">
              <div>
                <div className="mb-2.5 text-xs tracking-[0.08em] text-[#6d8195]">短信工作区</div>
                <h2>
                  消息记录 <span>{data?.messages.length ?? 0}</span>
                </h2>
              </div>
              <div className="flex gap-5 text-xs text-[#8a9aac] max-[1000px]:gap-3 [&_b]:ml-[5px] [&_b]:text-[15px] [&_b]:font-semibold [&_b]:text-[#163b5c]">
                <span>
                  收到 <b>{incoming}</b>
                </span>
                <span>
                  发出 <b>{outgoing}</b>
                </span>
              </div>
            </div>
            <div className="flex justify-between gap-4 border-b border-[#e9eff5] px-[26px] pb-5 max-[1000px]:flex-wrap max-[740px]:px-5 max-[740px]:pb-4 [&>.ant-input-search]:max-w-[270px] max-[1000px]:[&>.ant-input-search]:max-w-full">
              <Segmented
                options={['全部', '收件箱', '已发送']}
                value={filter}
                onChange={setFilter}
              />
              <Input.Search
                placeholder="搜索号码或短信内容"
                aria-label="搜索短信"
                allowClear
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            {!ready && (
              <Alert
                title={
                  wsStatus !== 'connected'
                    ? '正在连接本地服务…'
                    : '设备未连接，请检查 USB 连接和串口占用。'
                }
                type="warning"
                showIcon
              />
            )}
            {state.isError && (
              <Alert title="无法读取服务状态，等待连接恢复。" type="error" showIcon />
            )}
            <div className="max-h-[440px] min-h-[270px] overflow-auto max-[740px]:max-h-[400px]">
              {state.isPending ? (
                <div className="flex min-h-[265px] items-center justify-center">
                  <Spin />
                </div>
              ) : !messages.length ? (
                <div className="flex min-h-[265px] items-center justify-center">
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      search
                        ? '没有匹配的短信'
                        : filter === '已发送'
                          ? '还没有发送记录'
                          : '暂无短信，收到后会自动显示'
                    }
                  />
                </div>
              ) : (
                messages.map((item) => (
                  <article
                    key={item.id}
                    className={`flex gap-4 border-b border-[#eef2f6] px-[26px] py-5 transition-colors duration-150 last:border-b-0 max-[740px]:px-5 max-[740px]:py-[18px] ${selected === item.id ? 'bg-[#f4f8fd]' : ''}`}
                  >
                    <div
                      className={`grid size-9 shrink-0 place-items-center rounded-[11px] text-base ${item.direction === 'incoming' ? 'bg-[#eaf2fc] text-[#1766c2]' : 'bg-[#eaf6f2] text-[#197d68]'}`}
                    >
                      {item.direction === 'incoming' ? <ArrowDownOutlined /> : <ArrowUpOutlined />}
                    </div>
                    <div className="min-w-0 flex-1 [&_p]:my-[9px] [&_p]:text-sm [&_p]:leading-[1.8] [&_p]:whitespace-pre-wrap [&_p]:wrap-anywhere [&_p]:text-[#425b73]">
                      <div className="flex items-center justify-between gap-3 max-[1000px]:flex-col max-[1000px]:items-start max-[1000px]:gap-[5px] [&_strong]:text-sm [&_strong]:font-semibold [&_time]:shrink-0 [&_time]:text-[11px] [&_time]:text-[#8a9aac]">
                        <strong className="font-mono tracking-[-0.3px] tabular-nums">
                          {item.phone}
                        </strong>
                        <time>{time(item.timestamp)}</time>
                      </div>
                      <p>{item.content}</p>
                      <div className="flex min-h-[25px] items-center gap-[7px] [&_.ant-tag]:m-0! [&_.ant-tag]:border-0! [&_.ant-tag]:text-[11px]! [&_.ant-btn]:ml-auto [&_.ant-btn]:text-xs!">
                        <Tag color={states[item.state].color}>{states[item.state].label}</Tag>
                        {item.error && (
                          <Tooltip title={errors[item.error] || item.error}>
                            <span className="truncate text-[11px] text-[#aa6920]">
                              {errors[item.error] || item.error}
                            </span>
                          </Tooltip>
                        )}
                        {item.direction === 'incoming' && (
                          <Button
                            type="link"
                            size="small"
                            onClick={() => {
                              setSelected(item.id)
                              form.setFieldValue('to', item.phone)
                              document.getElementById('sms_content')?.focus()
                            }}
                          >
                            回复
                          </Button>
                        )}
                      </div>
                    </div>
                  </article>
                ))
              )}
            </div>
          </section>
          <section className="rounded-2xl border border-[#dce5ed] bg-white shadow-[0_3px_14px_#163b5c03] px-[26px] pt-[22px] pb-6 max-[740px]:p-5 [&_.ant-form-item]:mb-4! [&_.ant-input]:text-sm! [&_.ant-form-item-label_label]:text-xs! [&_.ant-form-item-label_label]:text-[#61758a]! [&_input.ant-input]:h-[38px]!">
            <div className="mb-5 flex items-center justify-between gap-3 [&_h2]:m-0 [&_h2]:text-base [&_h2]:font-semibold [&_.ant-badge-status-text]:text-xs!">
              <h2>
                <MessageOutlined /> 发送短信
              </h2>
              <span className="text-xs text-[#8a9aac]">使用当前 SIM 卡发送</span>
            </div>
            <Form
              form={form}
              name="sms"
              layout="vertical"
              onFinish={(values) => send.mutate(values)}
            >
              <Form.Item
                name="to"
                label="收件号码"
                rules={[
                  { required: true, message: '请输入收件号码' },
                  { pattern: /^\+?\d{5,20}$/, message: '请输入 5–20 位号码，可包含开头的 +' },
                ]}
              >
                <Input placeholder="输入手机号码" inputMode="tel" autoComplete="off" />
              </Form.Item>
              <Form.Item
                name="content"
                label="短信内容"
                rules={[
                  { required: true, whitespace: true, message: '请输入短信内容' },
                  {
                    validator: (_, value) =>
                      new TextEncoder().encode(value ?? '').length <= 512
                        ? Promise.resolve()
                        : Promise.reject(new Error('内容不能超过 512 UTF-8 字节')),
                  },
                ]}
              >
                <Input.TextArea rows={3} placeholder="写一条短信…" maxLength={512} />
              </Form.Item>
              <div className="flex items-center justify-between gap-[15px] [&>.ant-btn]:h-10! [&>.ant-btn]:min-w-[125px]">
                <span className={bytes > 512 ? 'text-xs text-[#c33939]' : 'text-xs text-[#8a9aac]'}>
                  {bytes} / 512 字节
                  <span className="max-[1000px]:hidden"> · 提交成功不等于送达确认</span>
                </span>
                <Button
                  type="primary"
                  htmlType="submit"
                  icon={<SendOutlined />}
                  loading={send.isPending || busy}
                  disabled={!ready || !mobile?.sms_ready || bytes > 512}
                >
                  {busy ? '等待发送结果' : '发送短信'}
                </Button>
              </div>
            </Form>
          </section>
        </div>
      </main>
      <footer className="flex justify-between px-[38px] pb-6 text-[11px] text-[#8a9aac] max-[1000px]:px-6 max-[1000px]:pb-5 max-[740px]:px-4 max-[740px]:[&>span]:hidden [&_.ant-typography]:text-[11px]! [&_i]:mr-[5px] [&_i]:inline-block [&_i]:size-1.5 [&_i]:rounded-full">
        <Typography.Text type="secondary">AIR780EHV · 本地设备工作台</Typography.Text>
        <span>
          <i className={ready ? 'bg-[#197d68]' : 'bg-[#9baaba]'} />{' '}
          {ready ? '设备事件实时同步' : '等待设备连接'}
        </span>
      </footer>
    </div>
  )
}
