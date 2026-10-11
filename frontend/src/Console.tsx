import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert,
  App,
  Badge,
  Breadcrumb,
  Button,
  Empty,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Segmented,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { MenuProps } from 'antd'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  CopyOutlined,
  DashboardOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PhoneOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  UsbOutlined,
} from '@ant-design/icons'
import { APP_NAME } from './constants'
import CallPanel from './CallPanel'
import AgentSettings from './AgentSettings'
import { connection, stateKey } from './connection'
import type { Sms, Snapshot } from './types'

const { Header, Content, Footer, Sider } = Layout
const menuItems: MenuProps['items'] = [
  { key: 'overview', label: '设备总览', icon: <DashboardOutlined /> },
  {
    key: 'call-group',
    label: '实时通话',
    icon: <PhoneOutlined />,
    children: [
      { key: 'call', label: '通话控制', icon: <PhoneOutlined /> },
      { key: 'agent', label: 'AGENT设置', icon: <RobotOutlined /> },
    ],
  },
  { key: 'sms', label: '短信管理', icon: <MessageOutlined /> },
]
const navigationLabels: Record<string, string> = {
  overview: '设备总览',
  call: '实时通话',
  sms: '短信管理',
  agent: 'AGENT设置',
}

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
  const [numberForm] = Form.useForm()
  const [numberSim, setNumberSim] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [narrow, setNarrow] = useState(false)
  const [navigation, setNavigation] = useState('overview')
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
  const saveNumber = useMutation({
    mutationFn: (values: { to: string }) =>
      connection.command('set_sim_number', { to: values.to.trim(), sim_id: numberSim ?? '' }),
    onSuccess: () => {
      setNumberSim(null)
      message.success('本机号码已保存，来源为人工确认')
    },
    onError: (error) => message.error(error.message),
  })
  const clearNumber = useMutation({
    mutationFn: () => connection.command('clear_sim_number', { sim_id: numberSim ?? '' }),
    onSuccess: () => {
      setNumberSim(null)
      message.success('已移除人工标注')
    },
    onError: (error) => message.error(error.message),
  })
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
  const navigateMenu: MenuProps['onClick'] = ({ key }) => {
    setNavigation(key)
    if (narrow) setCollapsed(true)
    window.scrollTo({ top: 0 })
  }
  return (
    <Layout className="min-h-screen! [&_:focus-visible]:outline-2 [&_:focus-visible]:outline-[#1766c2] [&_:focus-visible]:outline-offset-3 motion-reduce:[&_*]:transition-none! motion-reduce:[&_*]:animate-none!">
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        breakpoint="lg"
        collapsedWidth={narrow ? 0 : 80}
        onBreakpoint={(broken) => {
          setNarrow(broken)
          setCollapsed(broken)
        }}
        trigger={narrow ? null : undefined}
        className={narrow ? 'fixed! top-0 bottom-0 left-0 z-30' : 'sticky! top-0 h-screen'}
      >
        <a
          href="/"
          aria-label={APP_NAME}
          className="flex h-16 items-center justify-center gap-2 overflow-hidden whitespace-nowrap text-white!"
        >
          <UsbOutlined className="text-2xl" />
          {!collapsed && <strong className="text-lg tracking-wide">{APP_NAME}</strong>}
        </a>
        <Menu
          theme="dark"
          selectedKeys={[navigation]}
          defaultOpenKeys={['call-group']}
          mode="inline"
          items={menuItems}
          onClick={navigateMenu}
        />
      </Sider>
      {narrow && !collapsed && (
        <button
          className="fixed inset-0 z-20 border-0 bg-black/35"
          aria-label="关闭导航遮罩"
          onClick={() => setCollapsed(true)}
        />
      )}
      <Layout className="min-w-0">
        <Header className="flex items-center justify-between gap-3 border-b border-[#dce5ed] bg-white! px-6! leading-normal! max-[740px]:px-4!">
          <div className="flex min-w-0 items-center gap-3">
            <Button
              type="text"
              aria-label={collapsed ? '展开导航' : '收起导航'}
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((value) => !value)}
            />
            <span className="truncate font-semibold">{APP_NAME}</span>
          </div>
          <div className="flex items-center gap-4 max-[740px]:gap-2">
            <Tag variant="filled" className="max-[480px]:hidden!">
              本地开发
            </Tag>
            <Badge
              status={wsStatus === 'connected' ? 'success' : 'processing'}
              text={wsStatus === 'connected' ? '服务已连接' : '服务重连中'}
            />
          </div>
        </Header>
        <Content className="mx-6! max-[740px]:mx-4!">
          <Breadcrumb
            className="my-4!"
            items={[{ title: '控制台' }, { title: navigationLabels[navigation] }]}
          />
          {/* Keep the call owner mounted so menu navigation never interrupts audio. */}
          <div hidden={navigation !== 'call'}>
            <CallPanel
              device={data?.device ?? null}
              ready={ready}
              onAnswer={() => setNavigation('call')}
            />
          </div>
          <div hidden={navigation !== 'agent'}>
            <AgentSettings />
          </div>
          <div className="min-w-0">
            <aside
              id="device-overview"
              hidden={navigation !== 'overview'}
              className="grid min-w-0 grid-cols-2 gap-6 max-[900px]:grid-cols-1 max-[740px]:gap-4 [&[hidden]]:hidden"
            >
              <section className="p-6 max-[740px]:p-5 rounded-2xl border border-[#dce5ed] bg-white shadow-[0_3px_14px_#163b5c03]">
                <div className="flex items-center justify-between [&_h2]:m-0 [&_h2]:text-base [&_h2]:font-semibold">
                  <h2>连接状态</h2>
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
                  <p>
                    {mobile?.number_source === 'manual'
                      ? '人工确认 · 按当前 SIM 卡保存'
                      : mobile?.number
                        ? '来源：SIM 卡'
                        : '当前 SIM 未返回本机号码，可手动设置'}
                  </p>
                  <Button
                    className="mt-3"
                    size="small"
                    disabled={!ready || !mobile?.sim_ready || !mobile?.iccid}
                    onClick={() => {
                      numberForm.setFieldsValue({
                        to: mobile?.manual_number || mobile?.number || '',
                      })
                      setNumberSim(mobile?.iccid ?? null)
                    }}
                  >
                    {mobile?.manual_number ? '修改本机号码' : '设置本机号码'}
                  </Button>
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
            </aside>
            <div
              id="sms-workspace"
              hidden={navigation !== 'sms'}
              className="grid min-w-0 grid-cols-[minmax(0,1.5fr)_minmax(300px,1fr)] items-start gap-6 max-[1000px]:grid-cols-1 max-[740px]:gap-4 [&[hidden]]:hidden"
            >
              <section className="rounded-2xl border border-[#dce5ed] bg-white shadow-[0_3px_14px_#163b5c03] flex min-h-[420px] flex-1 flex-col overflow-hidden [&>.ant-alert]:mx-[26px] [&>.ant-alert]:mt-4">
                <div className="flex items-center justify-between px-[26px] pt-6 pb-5 max-[740px]:p-5 [&_h2]:m-0 [&_h2]:text-2xl [&_h2]:tracking-[-0.4px] [&_h2>span]:ml-[5px] [&_h2>span]:rounded-md [&_h2>span]:bg-[#edf3fa] [&_h2>span]:px-2 [&_h2>span]:py-[3px] [&_h2>span]:align-middle [&_h2>span]:text-[13px] [&_h2>span]:text-[#1766c2]">
                  <div>
                    <div className="mb-2.5 text-xs tracking-[0.08em] text-[#6d8195]">
                      短信工作区
                    </div>
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
                          {item.direction === 'incoming' ? (
                            <ArrowDownOutlined />
                          ) : (
                            <ArrowUpOutlined />
                          )}
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
                    <span
                      className={bytes > 512 ? 'text-xs text-[#c33939]' : 'text-xs text-[#8a9aac]'}
                    >
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
              <div className="col-span-full flex items-start gap-2 px-2 text-xs leading-6 text-[#6d8195] [&>.anticon]:mt-1.5 [&>.anticon]:text-[#197d68]">
                <CheckCircleOutlined />
                <span>短信保存在本机，重新打开页面可继续查看。</span>
              </div>
            </div>
          </div>
        </Content>
        <Footer className="px-4! text-center!">
          <Typography.Text type="secondary" className="text-xs!">
            {APP_NAME} ©{new Date().getFullYear()} · 本地控制台
          </Typography.Text>
        </Footer>
      </Layout>
      <Modal
        title="设置本机号码"
        open={numberSim !== null}
        onCancel={() => {
          if (!saveNumber.isPending && !clearNumber.isPending) setNumberSim(null)
        }}
        footer={null}
        destroyOnHidden
      >
        <p className="text-sm leading-6 text-slate-500">
          请填写设备发短信后，你在接收手机上看到的发件号码。该号码将标注为“人工确认”，仅保存到当前
          SIM 卡。
        </p>
        <Form form={numberForm} layout="vertical" onFinish={(values) => saveNumber.mutate(values)}>
          <p className="mb-4 text-sm leading-6 text-slate-500">
            当前短信数据仅提供发件人号码，未提供本机收件号码，暂时无法生成收件号码备选。
          </p>
          <Form.Item
            name="to"
            label="已确认的本机号码"
            rules={[
              { required: true, message: '请输入号码' },
              { pattern: /^\+?\d{5,20}$/, message: '请输入 5–20 位号码，可包含开头的 +' },
            ]}
          >
            <Input
              inputMode="tel"
              autoComplete="off"
              placeholder="填写手机上看到的设备发件号码"
              disabled={saveNumber.isPending || clearNumber.isPending}
            />
          </Form.Item>
          <div className="flex flex-wrap justify-end gap-2">
            {mobile?.manual_number && (
              <Button
                danger
                loading={clearNumber.isPending}
                disabled={saveNumber.isPending || !ready || mobile.iccid !== numberSim}
                onClick={() => clearNumber.mutate()}
              >
                移除人工标注
              </Button>
            )}
            <Button
              type="primary"
              htmlType="submit"
              loading={saveNumber.isPending}
              disabled={clearNumber.isPending || !ready || mobile?.iccid !== numberSim}
            >
              保存号码
            </Button>
          </div>
        </Form>
      </Modal>
    </Layout>
  )
}
