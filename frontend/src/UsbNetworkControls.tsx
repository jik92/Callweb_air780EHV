import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Alert, Select, Switch, Tag } from 'antd'
import { configureUsbNetwork } from './connection'
import type { UsbNetworkState } from './types'

export default function UsbNetworkControls({
  ready,
  supported,
  state,
  callBusy,
}: {
  ready: boolean
  supported: boolean
  state?: UsbNetworkState
  callBusy: boolean
}) {
  const [mode, setMode] = useState<'ecm' | 'rndis'>('ecm')
  const change = useMutation({
    mutationFn: (enabled: boolean) =>
      configureUsbNetwork(enabled, state?.enabled ? state.mode : mode),
  })
  const switching = change.isPending || state?.state === 'switching'
  return (
    <div className="mb-6 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="m-0 text-base font-semibold">USB 网卡 · 4G 网络共享</h3>
        <Switch
          aria-label="开启 USB 网卡模式"
          checked={state?.enabled === true}
          checkedChildren="开启"
          unCheckedChildren="关闭"
          loading={switching}
          disabled={!ready || !supported || callBusy || switching}
          onChange={(enabled) => change.mutate(enabled)}
        />
      </div>
      <p className="text-sm leading-6 text-slate-600">
        通过 USB 把 SIM 卡的 4G 网络提供给笔记本，使用 NAT
        模式。电脑需获取网卡地址后才能上网，会消耗 SIM 卡流量。
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Select<'ecm' | 'rndis'>
          aria-label="USB 网卡协议"
          className="w-full max-w-72"
          value={state?.enabled ? state.mode : mode}
          disabled={switching || state?.enabled}
          onChange={setMode}
          options={[
            { value: 'ecm', label: 'ECM（macOS / Linux）' },
            { value: 'rndis', label: 'RNDIS（Windows / Linux）' },
          ]}
        />
        <Tag color={state?.enabled ? 'green' : 'default'}>
          {state?.state === 'unknown'
            ? '结果待确认'
            : switching
              ? '正在切换'
              : state?.enabled
                ? '设备已启用'
                : '设备未启用'}
        </Tag>
      </div>
      <p className="mb-0 text-xs leading-5 text-slate-500">
        切换会短暂重新注册 4G、断开并重新枚举
        USB。关闭后等待串口重连；如控制台一直离线，松开所有按键并拔插 USB
        恢复。每次上电默认关闭，需手动开启。
      </p>
      {!ready && (
        <Alert
          className="mt-3!"
          type="info"
          showIcon
          title="等待 USB 设备连接；网卡是否可用请查看电脑网络设置"
        />
      )}
      {ready && !supported && (
        <Alert className="mt-3!" type="warning" showIcon title="需要更新设备脚本以支持 USB 网卡" />
      )}
      {callBusy && <Alert className="mt-3!" type="info" showIcon title="请结束通话后切换网卡" />}
      {(change.isError || state?.error) && (
        <Alert
          className="mt-3!"
          type="error"
          showIcon
          title={change.isError ? change.error.message : state?.error}
        />
      )}
    </div>
  )
}
