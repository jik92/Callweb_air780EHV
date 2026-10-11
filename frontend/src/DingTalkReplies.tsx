import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Checkbox, Form, Input, Spin, Switch, Tag, Typography } from 'antd'
import { getDingTalkReplies, saveDingTalkReplies } from './connection'

const key = ['dingtalk-replies']
type Values = Omit<Parameters<typeof saveDingTalkReplies>[0], 'user_ids'> & { users: string }
const statuses: Record<string, string> = {
  disabled: '未连接',
  connecting: '连接中',
  connected: 'Stream 已连接',
  reconnecting: '重连中',
}
const outcomes: Record<string, string> = {
  claimed: '已接收',
  pending: '等待推送',
  sent: '钉钉已接受',
  skipped: '已跳过',
  queued: '等待发送',
  sending: '发送中',
  submitted: '已提交短信中心',
  failed: '失败',
  unknown: '结果未知',
}
export default function DingTalkReplies({ visible }: { visible: boolean }) {
  const { message } = App.useApp()
  const cache = useQueryClient()
  const [form] = Form.useForm<Values>()
  const initialized = useRef(false)
  const query = useQuery({
    queryKey: key,
    queryFn: getDingTalkReplies,
    refetchInterval: visible ? 3000 : false,
  })
  useEffect(() => {
    if (query.data && !initialized.current) {
      form.setFieldsValue({
        ...query.data,
        client_secret: '',
        users: query.data.user_ids.join('\n'),
        clear_secret: false,
      })
      initialized.current = true
    }
  }, [query.data, form])
  const save = useMutation({
    mutationFn: (values: Values) => {
      const { users, ...data } = values
      return saveDingTalkReplies({
        ...data,
        push_enabled: data.push_enabled || false,
        push_conversation_id: data.push_conversation_id || '',
        robot_code: data.robot_code || '',
        user_ids: (users || '').split(/[\s,，]+/).filter(Boolean),
      })
    },
    onSuccess: (data) => {
      cache.setQueryData(key, data)
      form.setFieldsValue({ client_secret: '', clear_secret: false })
      message.success('Stream 机器人设置已保存')
    },
    onError: (error) => message.error(error.message),
  })
  return (
    <div className="mt-6 border-t border-slate-200 pt-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 text-base font-semibold">Stream 应用机器人 · 短信推送与回复</h2>
        <Tag color={query.data?.status === 'connected' ? 'green' : 'default'}>
          {statuses[query.data?.status || 'disabled']}
        </Tag>
      </div>
      <p className="text-sm leading-6 text-slate-500">
        使用同一个应用机器人推送新收到的短信，再引用推送并 @ 它回复。与上面的 Webhook
        模式独立，可以仅开启 Stream，也可分别启用推送和回复。
      </p>
      <Alert
        type="info"
        showIcon
        title="一个应用机器人即可完成推送和回复"
        description="填写应用凭据并开启连接，将应用机器人加入目标群。先在群里 @ 它发“测试”，使用下方来源填入推送目标群，再开启收件推送。回复开关独立；回复范围的群 ID 和用户 ID 留空表示允许全部。主动推送还需要应用拥有机器人发送群消息的权限。"
      />
      {query.isPending ? (
        <Spin />
      ) : query.isError ? (
        <Alert className="mt-4!" type="error" title={query.error.message} />
      ) : (
        query.data && (
          <div className="mt-5 grid min-w-0 grid-cols-2 gap-6 max-[900px]:grid-cols-1">
            <Form
              name="dingtalk-replies"
              form={form}
              initialValues={{ push_enabled: false, push_conversation_id: '', robot_code: '' }}
              layout="vertical"
              disabled={save.isPending}
              onFinish={save.mutate}
            >
              <Form.Item name="enabled" label="连接 Stream 机器人" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="client_id" label="应用 Client ID（AppKey）">
                <Input maxLength={200} />
              </Form.Item>
              <Form.Item
                name="client_secret"
                label="Client Secret（AppSecret）"
                extra={
                  query.data.has_secret
                    ? '已保存；留空保留原密钥。'
                    : '填写应用凭据，不能使用推送机器人的 SEC 密钥。'
                }
              >
                <Input.Password autoComplete="new-password" maxLength={500} />
              </Form.Item>
              <h3 className="text-sm font-semibold">收件短信推送</h3>
              <Form.Item
                name="push_enabled"
                label="通过应用机器人推送新短信"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>
              <Form.Item
                name="push_conversation_id"
                label="推送目标群 ID（可选）"
                extra="留空使用“允许回复的群 ID”；两个群 ID 都留空时不能推送。先在目标群 @ 机器人，也可点击右侧“使用此群推送”。"
              >
                <Input maxLength={300} placeholder="留空：使用允许回复的群 ID" />
              </Form.Item>
              <Form.Item
                name="robot_code"
                label="机器人编码（RobotCode，可选）"
                extra="留空使用 AppKey；可从最近一次 @ 的来源填入回调返回的编码。"
              >
                <Input maxLength={200} placeholder="留空使用 AppKey" />
              </Form.Item>
              <h3 className="text-sm font-semibold">@ 回复短信</h3>
              <Form.Item
                name="conversation_id"
                label="允许回复的群 ID（可选）"
                extra="留空允许机器人所在的所有群；填写后只允许该群。"
              >
                <Input maxLength={300} placeholder="留空：所有群" />
              </Form.Item>
              <Form.Item
                name="users"
                label="允许操作的用户 ID（可选）"
                extra="留空允许所有群成员；填写 senderStaffId 后只允许这些用户，每行一个，也可用逗号分隔。"
              >
                <Input.TextArea rows={3} placeholder="留空：所有用户" />
              </Form.Item>
              <Form.Item name="allow_sending" label="允许通过 @ 回复短信" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="clear_secret" valuePropName="checked">
                <Checkbox>清除应用密钥（须先关闭连接、推送与回复）</Checkbox>
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={save.isPending}>
                保存 Stream 机器人设置
              </Button>
              <p className="text-xs leading-6 text-slate-500">
                允许后，符合上述范围的群成员引用有效短信并 @ 机器人即可实际发送短信。两个 ID
                留空时，所有群成员均可操作。重复回调不重发；新发一条指令是新请求。
              </p>
              {save.isError && <Alert type="error" title={save.error.message} />}
            </Form>
            <div className="min-w-0">
              {query.data.error && <Alert type="warning" showIcon title={query.data.error} />}
              <h3 className="text-sm font-semibold">最近一次群内 @ 的来源</h3>
              {query.data.last_sender ? (
                <div className="rounded-xl bg-slate-50 p-3 text-sm">
                  <p className="m-0">群 ID</p>
                  <Typography.Paragraph className="wrap-anywhere" copyable>
                    {query.data.last_sender.conversation_id || '未提供'}
                  </Typography.Paragraph>
                  <p className="m-0">用户 ID</p>
                  <Typography.Paragraph className="mb-0! wrap-anywhere" copyable>
                    {query.data.last_sender.user_id || '未提供'}
                  </Typography.Paragraph>
                  {query.data.last_sender.robot_code && (
                    <>
                      <p className="mt-3 mb-0">机器人编码</p>
                      <Typography.Paragraph className="wrap-anywhere" copyable>
                        {query.data.last_sender.robot_code}
                      </Typography.Paragraph>
                    </>
                  )}
                  <Button
                    className="mt-3"
                    disabled={!query.data.last_sender.conversation_id || save.isPending}
                    onClick={() => {
                      form.setFieldsValue({
                        push_conversation_id: query.data.last_sender?.conversation_id || '',
                        robot_code:
                          query.data.last_sender?.robot_code || form.getFieldValue('robot_code'),
                      })
                      message.info('目标群已填入，请开启收件推送并保存')
                    }}
                  >
                    使用此群推送
                  </Button>
                </div>
              ) : (
                <p className="text-sm text-slate-500">
                  连接后在群内 @ 机器人，这里会显示来源。此步骤不发送短信。
                </p>
              )}
              <p className="text-xs leading-6 text-slate-500">
                若钉钉没有传回引用信息，使用「@机器人 回复 短信编号
                回复内容」。编号来自新推送；旧推送未包含编号时不能直接回复。更换 SIM 后不能回复原
                SIM 的短信。
              </p>
              <h3 className="text-sm font-semibold">最近 Stream 收件推送</h3>
              {!query.data.deliveries?.length && (
                <p className="text-sm text-slate-500">暂无推送记录</p>
              )}
              <div className="max-h-64 overflow-y-auto">
                {query.data.deliveries?.map((item) => (
                  <div key={item.message_id} className="border-b border-slate-100 py-3 text-sm">
                    <div className="flex flex-wrap justify-between gap-2">
                      <span className="font-mono text-xs">{item.message_id.slice(3, 19)}</span>
                      <Tag>{outcomes[item.state] || item.state}</Tag>
                    </div>
                    <p className="my-1 wrap-anywhere text-xs text-slate-500">
                      目标群：{item.conversation_id}
                    </p>
                    <span className="text-xs text-slate-500">
                      {new Date(item.updated_at * 1000).toLocaleString('zh-CN')}
                    </span>
                    {item.error && (
                      <p className="mb-0 wrap-anywhere text-xs text-red-600">{item.error}</p>
                    )}
                  </div>
                ))}
              </div>
              <h3 className="text-sm font-semibold">最近回复记录</h3>
              {!query.data.requests.length && (
                <p className="text-sm text-slate-500">暂无回复请求</p>
              )}
              <div className="max-h-80 overflow-y-auto">
                {query.data.requests.map((item) => (
                  <div key={item.request_id} className="border-b border-slate-100 py-3 text-sm">
                    <div className="flex flex-wrap justify-between gap-2">
                      <span className="font-mono text-xs">{item.reference || '未匹配短信'}</span>
                      <Tag>{outcomes[item.state] || item.state}</Tag>
                    </div>
                    <span className="text-xs text-slate-500">
                      {new Date(item.updated_at * 1000).toLocaleString('zh-CN')}
                    </span>
                    {item.error && (
                      <p className="mb-0 wrap-anywhere text-xs text-red-600">{item.error}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )
      )}
    </div>
  )
}
