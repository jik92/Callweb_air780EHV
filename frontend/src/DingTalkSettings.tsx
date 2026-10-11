import { useEffect, useRef } from 'react'
import DingTalkReplies from './DingTalkReplies'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Checkbox, Form, Input, Spin, Switch, Tag } from 'antd'
import { BellOutlined, SaveOutlined, SendOutlined } from '@ant-design/icons'
import {
  dingtalkSettingsKey,
  getDingTalkSettings,
  saveDingTalkSettings,
  testDingTalkPush,
} from './connection'

type Values = Parameters<typeof saveDingTalkSettings>[0]
const states: Record<string, { label: string; color: string }> = {
  pending: { label: '等待推送', color: 'gold' },
  sending: { label: '推送中', color: 'blue' },
  sent: { label: '钉钉已接受', color: 'green' },
  failed: { label: '推送失败', color: 'red' },
  unknown: { label: '结果未知', color: 'orange' },
  skipped: { label: '已跳过', color: 'default' },
}

export default function DingTalkSettings({ visible }: { visible: boolean }) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<Values>()
  const initialized = useRef(false)
  const settings = useQuery({
    queryKey: dingtalkSettingsKey,
    queryFn: getDingTalkSettings,
    refetchInterval: visible ? 5000 : false,
  })
  useEffect(() => {
    if (settings.data && !initialized.current) {
      form.setFieldsValue({
        enabled: settings.data.enabled,
        webhook: '',
        secret: '',
        clear_credentials: false,
      })
      initialized.current = true
    }
  }, [settings.data, form])
  const save = useMutation({
    mutationFn: saveDingTalkSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(dingtalkSettingsKey, data)
      form.setFieldsValue({ webhook: '', secret: '', clear_credentials: false })
      message.success('钉钉推送设置已保存')
    },
    onError: (error) => message.error(error.message),
  })
  const test = useMutation({
    mutationFn: testDingTalkPush,
    onSuccess: (data) => message.success(data.message),
    onError: (error) => message.error(error.message),
  })
  const busy = save.isPending || test.isPending
  return (
    <section className="mb-6 min-w-0 rounded-2xl border border-[#dce5ed] bg-white p-6 max-[740px]:p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="m-0 flex items-center gap-2 text-base font-semibold">
          <BellOutlined /> Webhook 短信推送
        </h2>
        <Tag color={settings.data?.ready ? 'green' : 'default'}>
          {settings.data?.ready ? '新短信自动推送' : '推送未启用'}
        </Tag>
      </div>
      <p className="mb-5 text-sm leading-6 text-slate-500">
        Webhook 模式与下方 Stream
        模式独立。启用后，新收到的短信会将发送号码、接收时间和完整内容推送到指定钉钉群。历史收件箱同步和已发送短信不会推送。
      </p>
      {settings.isPending ? (
        <Spin />
      ) : settings.isError ? (
        <Alert
          type="error"
          showIcon
          title="无法读取钉钉推送设置"
          action={<Button onClick={() => void settings.refetch()}>重试</Button>}
        />
      ) : (
        <div className="grid min-w-0 grid-cols-2 gap-6 max-[900px]:grid-cols-1">
          <Form form={form} layout="vertical" disabled={busy} onFinish={save.mutate}>
            <Form.Item name="enabled" label="启用 Webhook 短信推送" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item
              name="webhook"
              label="机器人 Webhook"
              extra={
                settings.data.has_webhook
                  ? '已在本机保存；留空保留原地址。'
                  : '填写钉钉自定义机器人的 Webhook 地址。'
              }
            >
              <Input.Password
                autoComplete="new-password"
                placeholder="https://oapi.dingtalk.com/robot/send?access_token=…"
                maxLength={500}
              />
            </Form.Item>
            <Form.Item
              name="secret"
              label="加签密钥"
              extra={
                settings.data.has_secret
                  ? '已在本机保存；留空保留原密钥。'
                  : '在机器人安全设置中选择「加签」，填写 SEC 开头的密钥。'
              }
            >
              <Input.Password autoComplete="new-password" placeholder="SEC…" maxLength={200} />
            </Form.Item>
            <Form.Item name="clear_credentials" valuePropName="checked">
              <Checkbox>清除本机保存的 Webhook 和密钥（须先关闭推送）</Checkbox>
            </Form.Item>
            <div className="flex flex-wrap gap-3">
              <Button
                type="primary"
                htmlType="submit"
                icon={<SaveOutlined />}
                loading={save.isPending}
              >
                保存设置
              </Button>
              <Button
                icon={<SendOutlined />}
                loading={test.isPending}
                disabled={busy || !settings.data.has_webhook || !settings.data.has_secret}
                onClick={() => test.mutate()}
              >
                测试已保存的推送
              </Button>
            </div>
            <p className="mt-3 text-xs leading-6 text-slate-500">
              测试会向钉钉群发送一条固定测试消息，不包含真实短信。凭据仅保存在本机，不会回传到页面。
            </p>
            {(save.isError || test.isError) && (
              <Alert type="error" showIcon title={(save.error || test.error)?.message} />
            )}
          </Form>
          <div className="min-w-0">
            <Alert
              showIcon
              type="info"
              title="自动去重，不自动重试"
              description="相同短信只提交一次推送。网络超时或服务重启导致的未知结果会保留；推送失败不会影响短信接收和保存。"
            />
            <h3 className="mb-3 mt-5 text-sm font-semibold">最近推送记录</h3>
            {!settings.data.deliveries.length && (
              <p className="text-sm text-slate-500">暂无推送记录</p>
            )}
            <div className="max-h-96 overflow-y-auto">
              {settings.data.deliveries.map((item) => (
                <div key={item.message_id} className="border-b border-slate-100 py-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs text-slate-500">
                      {new Date(item.updated_at * 1000).toLocaleString('zh-CN', { hour12: false })}
                    </span>
                    <Tag color={states[item.state]?.color}>
                      {states[item.state]?.label || item.state}
                    </Tag>
                  </div>
                  {item.error && (
                    <p className="mb-0 mt-2 wrap-anywhere text-xs text-red-600">{item.error}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      <DingTalkReplies visible={visible} />
    </section>
  )
}
