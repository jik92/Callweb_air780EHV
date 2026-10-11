import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Empty, Form, Input, Modal, Segmented, Spin, Tag } from 'antd'
import {
  CloudOutlined,
  DownloadOutlined,
  PlusOutlined,
  ReloadOutlined,
  UsbOutlined,
} from '@ant-design/icons'
import { bindDevice, devicesKey } from './connection'
import type { ManagedDevice } from './connection'

export default function DeviceManagement({
  devices,
  loading,
  error,
  onRefresh,
  onSelect,
}: {
  devices: ManagedDevice[]
  loading: boolean
  error: boolean
  onRefresh: () => void
  onSelect: (device: ManagedDevice) => void
}) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('在线设备')
  const [search, setSearch] = useState('')
  const [binding, setBinding] = useState(false)
  const [provision, setProvision] = useState<Awaited<ReturnType<typeof bindDevice>> | null>(null)
  const [form] = Form.useForm()
  const create = useMutation({
    mutationFn: bindDevice,
    onSuccess: (data) => {
      setProvision(data)
      setBinding(false)
      form.resetFields()
      void queryClient.invalidateQueries({ queryKey: devicesKey })
      message.success('设备已绑定，启动本地设备 Agent 后显示在线')
    },
    onError: (error) => message.error(error.message),
  })
  const shown = devices.filter(
    (device) =>
      (filter === '全部绑定' || device.online) && `${device.name} ${device.id}`.includes(search),
  )
  function download() {
    if (!provision) return
    const content = JSON.stringify({ ...provision, serial_port: null }, null, 2) + '\n'
    const url = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'device-agent.json'
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return (
    <section className="mb-6 min-w-0 rounded-2xl border border-[#dce5ed] bg-white p-6 max-[740px]:p-5">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
        <h1 className="m-0 text-xl font-semibold">设备管理</h1>
        <div className="flex flex-wrap gap-2">
          <Button icon={<ReloadOutlined />} onClick={onRefresh}>
            刷新列表
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setBinding(true)}>
            绑定设备
          </Button>
        </div>
      </div>
      <p className="mb-5 text-sm leading-6 text-slate-500">
        设备登录后自动显示在线，选择设备查看总览并管理它的通话、短信和回复场景。
      </p>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <Segmented value={filter} onChange={setFilter} options={['在线设备', '全部绑定']} />
        <Input.Search
          className="w-72! max-w-full"
          aria-label="搜索设备"
          placeholder="搜索设备名称或编号"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          allowClear
        />
      </div>
      <div className="mb-4 flex flex-wrap gap-3 text-xs text-slate-500">
        <span>在线 {devices.filter((device) => device.online).length} 台</span>
        <span>已绑定 {devices.length} 台</span>
      </div>
      {error && (
        <Alert type="error" showIcon title="设备列表读取失败，请刷新重试" className="mb-4!" />
      )}
      {loading ? (
        <Spin />
      ) : !shown.length ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            search
              ? '没有匹配的设备'
              : filter === '在线设备'
                ? '暂无在线设备，启动设备 Agent 后会在这里显示'
                : '暂无绑定设备'
          }
        />
      ) : (
        <div className="grid min-w-0 grid-cols-2 gap-4 max-[1000px]:grid-cols-1">
          {shown.map((device) => (
            <button
              key={device.id}
              type="button"
              onClick={() => onSelect(device)}
              aria-label={`查看设备 ${device.name}`}
              className="min-w-0 cursor-pointer rounded-xl border border-slate-200 bg-white p-5 text-left text-inherit transition-colors hover:border-blue-400 hover:bg-blue-50/40 focus-visible:border-blue-500"
            >
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  {device.transport === 'websocket' ? (
                    <CloudOutlined className="text-xl text-blue-600" />
                  ) : (
                    <UsbOutlined className="text-xl text-blue-600" />
                  )}
                  <strong className="truncate text-base">{device.name}</strong>
                </div>
                <Tag color={device.online ? 'green' : 'default'}>
                  {device.online ? (device.ready ? '在线' : '已登录 · 同步中') : '离线'}
                </Tag>
              </div>
              <p className="mb-3 break-all font-mono text-xs text-slate-400">{device.id}</p>
              <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-500">
                <span>
                  {device.transport === 'websocket' ? 'USB · 设备 Agent' : '本机 USB 设备'}
                </span>
                <span>版本 {device.version || '—'}</span>
                {device.call_active && <span className="text-blue-600">设备有通话</span>}
              </div>
              <div className="mt-4 flex flex-wrap justify-between gap-2 border-t border-slate-100 pt-3 text-xs">
                <span className="text-slate-400">
                  最近在线{' '}
                  {device.last_seen
                    ? new Date(device.last_seen * 1000).toLocaleString('zh-CN', { hour12: false })
                    : '尚未登录'}
                </span>
                <span className="font-medium text-blue-600">查看设备总览 →</span>
              </div>
            </button>
          ))}
        </div>
      )}
      <Modal
        title="绑定远程设备"
        open={binding}
        onCancel={() => {
          if (!create.isPending) setBinding(false)
        }}
        footer={null}
      >
        <p className="text-sm leading-6 text-slate-500">
          为每台 USB 设备创建独立绑定。下载配置，在连接设备的电脑启动设备 Agent，通过 WS 登录 API。
        </p>
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => create.mutate(values)}
          disabled={create.isPending}
        >
          <Form.Item
            name="name"
            label="设备名称"
            rules={[{ required: true, whitespace: true, message: '请输入设备名称' }]}
          >
            <Input maxLength={60} placeholder="例如：客服设备 01" />
          </Form.Item>
          <Form.Item
            name="server_url"
            label="服务器 WS 地址"
            rules={[{ required: true, message: '请输入部署后的设备连接地址' }]}
            extra="支持 WS 与 WSS，推荐使用 WSS；地址路径为 /ws/device。"
          >
            <Input placeholder="wss://你的域名/ws/device" maxLength={500} />
          </Form.Item>
          {create.isError && (
            <Alert type="error" showIcon title={create.error.message} className="mb-4!" />
          )}
          <Button type="primary" htmlType="submit" loading={create.isPending}>
            创建绑定
          </Button>
        </Form>
      </Modal>
      <Modal
        title="设备绑定配置"
        open={!!provision}
        onCancel={() => setProvision(null)}
        footer={null}
      >
        <Alert
          type="success"
          showIcon
          title="绑定已创建，等待设备登录"
          description="配置包含设备的独立登录凭据，仅在本次创建时提供。请下载到连接 USB 的电脑，使用此配置启动设备 Agent。"
        />
        <p className="break-all font-mono text-xs text-slate-500">{provision?.device_id}</p>
        <Button type="primary" icon={<DownloadOutlined />} onClick={download}>
          下载 device-agent.json
        </Button>
      </Modal>
    </section>
  )
}
