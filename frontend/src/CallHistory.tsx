import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Empty, Modal, Table, Tag } from 'antd'
import { DownloadOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'

type Record = {
  id: string
  phone: string
  started_at: number
  connected_at: number | null
  state: string
  reason: string | null
  duration: number
  sample_rate: number | null
  recording_available: boolean
  recording_error: string | null
}
export const callHistoryKey = ['call-history']
const labels: { [key: string]: string } = {
  dialing: '拨号中',
  connected: '已接通',
  streaming: '通话中',
  hanging_up: '挂断中',
  ended: '已结束',
  failed: '未接通',
  unknown: '结果未知',
}
const reasons: { [key: string]: string } = {
  user_hangup: '主动挂断',
  browser_disconnected: '页面关闭',
  audio_timeout: '音频超时',
  browser_audio_timeout: '网页音频超时',
  answer_timeout: '接听超时',
  call_timeout: '达到通话时限',
  service_restarted: '服务重启',
  serial_disconnected: '设备断开',
  DISCONNECTED: '通话断开',
  HANGUP_CALL_DONE: '挂断完成',
  MAKE_CALL_FAILED: '拨号失败',
}
export default function CallHistory({ active }: { active: boolean }) {
  const [selected, setSelected] = useState<Record | null>(null)
  const player = useRef<HTMLAudioElement>(null)
  const [playbackError, setPlaybackError] = useState(false)
  useEffect(() => {
    if (active) {
      player.current?.pause()
      setSelected(null)
    }
  }, [active])
  const history = useQuery<Record[]>({
    queryKey: callHistoryKey,
    queryFn: async () => {
      const response = await fetch('/api/calls')
      if (!response.ok) throw new Error('无法读取通话记录')
      return response.json()
    },
    refetchInterval: active ? 5000 : false,
  })
  const recordingUrl = selected ? `/api/calls/${selected.id}/recording` : ''
  return (
    <section className="mb-6 rounded-2xl border border-[#dce5ed] bg-white p-6 max-[740px]:p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="m-0 text-base font-semibold">通话记录与录音</h2>
        <Button
          icon={<ReloadOutlined />}
          loading={history.isFetching}
          onClick={() => void history.refetch()}
        >
          刷新记录
        </Button>
      </div>
      <p className="mb-4 text-xs leading-6 text-[#6d8195]">
        每次拨号保存时间和结果。接通后的双向录音保存在本机，可在通话结束后回放。此功能上线前的电话没有录音。
      </p>
      {history.isError && (
        <Alert className="mb-3!" type="error" showIcon title="无法读取通话记录，请点击刷新重试。" />
      )}
      <Table<Record>
        rowKey="id"
        dataSource={history.data ?? []}
        loading={history.isPending}
        pagination={{ pageSize: 10, showSizeChanger: false, hideOnSinglePage: true }}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无通话记录，下一次拨号后自动保存"
            />
          ),
        }}
        columns={[
          {
            title: '号码 / 拨号时间',
            key: 'phone',
            render: (_, record) => (
              <div className="min-w-0">
                <div className="break-all font-mono text-sm">{record.phone}</div>
                <div className="mt-1 text-xs text-slate-400">
                  {new Date(record.started_at * 1000).toLocaleString('zh-CN', { hour12: false })}
                </div>
              </div>
            ),
          },
          {
            title: '结果',
            key: 'state',
            render: (_, record) => (
              <div>
                <Tag
                  color={
                    record.state === 'failed'
                      ? 'red'
                      : record.state === 'unknown'
                        ? 'orange'
                        : record.state === 'ended'
                          ? 'default'
                          : 'blue'
                  }
                >
                  {labels[record.state] ?? record.state}
                </Tag>
                <div className="mt-1 text-xs text-slate-400">
                  {record.connected_at
                    ? `${Math.floor(record.duration)} 秒`
                    : (reasons[record.reason ?? ''] ?? record.reason ?? '等待接通')}
                </div>
              </div>
            ),
          },
          {
            title: '录音',
            key: 'recording',
            render: (_, record) => (
              <div>
                <Button
                  size="small"
                  icon={<PlayCircleOutlined />}
                  disabled={active || !record.recording_available}
                  onClick={() => {
                    setPlaybackError(false)
                    setSelected(record)
                  }}
                >
                  回放
                </Button>
                {!record.recording_available && (
                  <div className="mt-1 text-xs text-slate-400">
                    {record.recording_error
                      ? '录音保存异常'
                      : ['dialing', 'connected', 'streaming', 'hanging_up'].includes(record.state)
                        ? '通话结束后可用'
                        : '无录音'}
                  </div>
                )}
              </div>
            ),
          },
        ]}
      />
      <Modal
        title="通话录音回放"
        open={!!selected}
        onCancel={() => {
          player.current?.pause()
          setSelected(null)
        }}
        footer={null}
        destroyOnHidden
      >
        {selected && (
          <>
            <p className="font-mono">{selected.phone}</p>
            <p className="text-xs leading-6 text-slate-500">
              {new Date(selected.started_at * 1000).toLocaleString('zh-CN', { hour12: false })} ·{' '}
              {Math.floor(selected.duration)} 秒 ·{' '}
              {reasons[selected.reason ?? ''] ?? selected.reason ?? labels[selected.state]}
            </p>
            <p className="text-xs leading-6 text-slate-500">
              左声道为网页麦克风和通话按键音，右声道为手机端音频。录音反映本机收发内容。
            </p>
            {selected.recording_error && (
              <Alert
                className="mb-3!"
                showIcon
                type="warning"
                title="录音保存异常，现有录音可能不完整。"
              />
            )}
            {playbackError && (
              <Alert
                className="mb-3!"
                type="error"
                showIcon
                title="录音加载失败，请关闭窗口后刷新记录重试。"
              />
            )}
            <audio
              key={selected.id}
              ref={player}
              controls
              preload="metadata"
              src={recordingUrl}
              className="w-full"
              onError={() => setPlaybackError(true)}
            />
            <Button
              className="mt-4"
              href={recordingUrl}
              download={`call-${selected.id}.wav`}
              icon={<DownloadOutlined />}
            >
              下载录音
            </Button>
          </>
        )}
      </Modal>
    </section>
  )
}
