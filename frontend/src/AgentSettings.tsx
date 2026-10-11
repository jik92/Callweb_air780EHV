import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Checkbox, Form, Input, Select, Spin, Switch, Tag } from 'antd'
import { RobotOutlined, SaveOutlined, LinkOutlined } from '@ant-design/icons'
import {
  agentSettingsKey,
  getAgentSettings,
  saveAgentSettings,
  testAgentConnection,
} from './connection'

type Values = Parameters<typeof saveAgentSettings>[0]
const voices = [
  'marin',
  'cedar',
  'alloy',
  'ash',
  'ballad',
  'coral',
  'echo',
  'sage',
  'shimmer',
  'verse',
]

export default function AgentSettings() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<Values>()
  const initialized = useRef(false)
  const settings = useQuery({ queryKey: agentSettingsKey, queryFn: getAgentSettings })
  useEffect(() => {
    if (settings.data && !initialized.current) {
      form.setFieldsValue({ ...settings.data, api_key: '', clear_api_key: false })
      initialized.current = true
    }
  }, [settings.data, form])
  const save = useMutation({
    mutationFn: saveAgentSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(agentSettingsKey, data)
      form.setFieldsValue({ api_key: '', clear_api_key: false })
      message.success('Agent配置和 AGENT.md 已保存，下一通 Agent电话生效')
    },
    onError: (error) => message.error(error.message),
  })
  const test = useMutation({
    mutationFn: testAgentConnection,
    onSuccess: (data) => message.success(data.message),
    onError: (error) => message.error(error.message),
  })
  const busy = save.isPending || test.isPending
  return (
    <section className="mb-6 min-w-0 rounded-2xl border border-[#dce5ed] bg-white p-6 max-[740px]:p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="m-0 flex items-center gap-2 text-base font-semibold">
          <RobotOutlined /> AGENT设置
        </h2>
        <Tag color={settings.data?.ready ? 'green' : 'default'}>
          {settings.data?.ready ? '已配置 · 可选择 Agent接听' : '尚未启用或未配置 API Key'}
        </Tag>
      </div>
      <p className="mb-5 text-sm leading-6 text-slate-500">
        来电时手动选择「Agent接听」，由 Realtime 按 AGENT.md
        回复。普通「接听」仍使用电脑麦克风。保存设置不会自动接电话。
      </p>
      {settings.isPending ? (
        <Spin />
      ) : settings.isError ? (
        <Alert
          type="error"
          showIcon
          title="无法读取 Agent配置"
          action={<Button onClick={() => void settings.refetch()}>重试</Button>}
        />
      ) : (
        <Form
          form={form}
          layout="vertical"
          disabled={busy}
          onFinish={(values) => save.mutate(values)}
        >
          <div className="grid min-w-0 grid-cols-[minmax(220px,1fr)_minmax(0,2fr)] gap-6 max-[900px]:grid-cols-1">
            <div className="min-w-0">
              <Form.Item name="enabled" label="启用 Agent接听选项" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="provider" label="接口提供方" rules={[{ required: true }]}>
                <Select
                  options={[
                    { value: 'aihubmix', label: 'AiHubMix' },
                    { value: 'openai', label: 'OpenAI 官方' },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="api_key"
                label="API Key"
                extra={
                  settings.data?.has_api_key
                    ? `已配置（${settings.data.key_source === 'environment' ? '后台环境变量' : '本机保存'}）；留空保留原 Key。`
                    : 'Key 仅保存于本机后台，不会返回到页面。'
                }
              >
                <Input.Password autoComplete="new-password" placeholder="输入 API Key" />
              </Form.Item>
              {settings.data?.key_source === 'local' && (
                <Form.Item name="clear_api_key" valuePropName="checked">
                  <Checkbox>移除本机保存的 Key</Checkbox>
                </Form.Item>
              )}
              <Form.Item
                name="model"
                label="Realtime模型"
                rules={[
                  { required: true },
                  { pattern: /^gpt-realtime[a-zA-Z0-9._-]*$/, message: '请填写 Realtime模型 ID' },
                ]}
                extra="默认 gpt-realtime-2.1，可填写账号支持的 Realtime模型 ID。"
              >
                <Input placeholder="gpt-realtime-2.1" maxLength={100} />
              </Form.Item>
              <Form.Item name="voice" label="声音" rules={[{ required: true }]}>
                <Select options={voices.map((value) => ({ value, label: value }))} />
              </Form.Item>
              <Form.Item
                name="greeting"
                label="开场白"
                extra="留空时先听来电人说话，再根据场景回复。"
              >
                <Input.TextArea rows={3} maxLength={500} placeholder="您好，我是 AI 电话助手…" />
              </Form.Item>
              <p className="text-xs leading-6 text-slate-500">
                选择 Agent接听后，通话音频和场景指令会发送至所选提供方，产生 API
                费用。接听页面关闭或服务连接失败时会请求挂断。
              </p>
            </div>
            <div className="min-w-0">
              <Form.Item
                name="instructions"
                label="AGENT.md 场景指令"
                rules={[{ required: true, whitespace: true, message: '请填写场景指令' }]}
                extra="填写身份、业务背景、常见问题和回复规则；保存到本机 data/agent/AGENT.md，每通 Agent电话开始时读取。"
              >
                <Input.TextArea
                  rows={19}
                  maxLength={32000}
                  className="font-mono! text-sm!"
                  placeholder="# 电话助手\n填写你的业务场景和标准回复…"
                />
              </Form.Item>
              <Alert
                type="info"
                showIcon
                title="修改配置从下一通电话生效"
                description="当前通话继续使用接听时的配置。Agent仅提供语音回复，不执行拨号、短信或其他业务操作。"
              />
            </div>
          </div>
          {save.isError && (
            <Alert className="mt-4!" showIcon type="error" title={save.error.message} />
          )}
          {test.isError && (
            <Alert className="mt-4!" showIcon type="error" title={test.error.message} />
          )}
          <div className="mt-5 flex flex-wrap gap-3">
            <Button
              type="primary"
              htmlType="submit"
              icon={<SaveOutlined />}
              loading={save.isPending}
            >
              保存设置
            </Button>
            <Button
              icon={<LinkOutlined />}
              loading={test.isPending}
              disabled={busy || !settings.data?.ready}
              onClick={() => test.mutate()}
            >
              验证已保存的连接
            </Button>
          </div>
          <p className="mb-0 mt-3 text-xs text-slate-500">
            连接验证只检查已保存的 Realtime配置，不拨号、不接听，也不发送通话音频。
          </p>
        </Form>
      )}
    </section>
  )
}
