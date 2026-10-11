import { useMutation } from '@tanstack/react-query'
import { Alert, Button } from 'antd'
import { EnvironmentOutlined } from '@ant-design/icons'
import { refreshDeviceLocation } from './connection'
import type { DeviceLocation as LocationState } from './types'

export default function DeviceLocation({
  location,
  ready,
  supported,
  callBusy,
}: {
  location?: LocationState
  ready: boolean
  supported: boolean
  callBusy: boolean
}) {
  const update = useMutation({ mutationFn: refreshDeviceLocation })
  const locating = location?.state === 'locating' || location?.state === 'resolving'
  return (
    <div className="mt-4 min-w-0 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">
          <EnvironmentOutlined /> 基站位置
        </span>
        <Button
          size="small"
          onClick={() => update.mutate()}
          loading={locating || update.isPending}
          disabled={!ready || !supported || callBusy}
        >
          更新位置
        </Button>
      </div>
      <p className="my-2 break-words text-sm text-slate-700" aria-live="polite">
        {!supported
          ? '更新设备脚本后可定位'
          : location?.state === 'locating'
            ? '正在获取基站坐标…'
            : location?.state === 'resolving'
              ? '正在解析地理位置…'
              : location?.address || location?.error || '尚未定位，点击更新位置'}
      </p>
      {location?.latitude !== undefined && location.longitude !== undefined && (
        <p className="my-1 break-all text-xs text-slate-500">
          {location.latitude.toFixed(6)}, {location.longitude.toFixed(6)} · WGS84
        </p>
      )}
      {location?.located_at && (
        <p className="my-1 text-xs text-slate-500">
          定位时间：{new Date(location.located_at * 1000).toLocaleString('zh-CN')}
        </p>
      )}
      <p className="mb-0 mt-2 text-xs leading-5 text-slate-500">
        单基站定位为大致位置。查询向合宙发送设备与基站信息，坐标由 OpenStreetMap 解析地址。{' '}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
          © OpenStreetMap contributors
        </a>
      </p>
      {callBusy && <p className="mb-0 mt-2 text-xs text-slate-500">通话结束后可更新位置</p>}
      {update.isError && (
        <Alert className="mt-3!" type="error" showIcon title={update.error.message} />
      )}
    </div>
  )
}
