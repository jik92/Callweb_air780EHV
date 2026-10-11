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
        user_ids: (users || '').split(/[\s,，]+/).filter(Boolean),
      })
    },
    onSuccess: (data) => {
      cache.setQueryData(key, data)
      form.setFieldsValue({ client_secret: '', clear_secret: false })
      message.success('机器人回复设置已保存')
    },
    onError: (error) => message.error(error.message),
  })
  return (
    <div className="mt-6 border-t border-slate-200 pt-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 text-base font-semibold">@ 机器人回复短信</h2>
        <Tag color={query.data?.status === 'connected' ? 'green' : 'default'}>
          {statuses[query.data?.status || 'disabled']}
        </Tag>
      </div>
      <p className="text-sm leading-6 text-slate-500">
        引用本应用推送的短信，@ Stream
        应用机器人，填写回复内容。按短信编号匹配本机原始号码，不会猜测收件人。
      </p>
      <Alert
        type="info"
        showIcon
        title="连接后开启回复"
        description="上面的自定义 Webhook 只负责推送。请另建 Stream 应用机器人并加入同一群。填写应用凭据并开启连接。群 ID 和用户 ID 可留空，表示允许机器人所在的所有群和这些群中的所有用户；如需限制范围，在群内 @ 它后从下方获取 ID。"
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
                <Checkbox>清除应用密钥（须先关闭连接与回复）</Checkbox>
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={save.isPending}>
                保存机器人回复设置
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
