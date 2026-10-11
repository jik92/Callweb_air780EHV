import { useMutation } from '@tanstack/react-query'
import { Alert, Button, Form, Input, Tag } from 'antd'
import { GlobalOutlined } from '@ant-design/icons'
import { testDeviceNetwork } from './connection'

export default function NetworkTest({ ready, supported }: { ready: boolean; supported: boolean }) {
  const test = useMutation({ mutationFn: testDeviceNetwork })
  const result = test.data
  return (
    <section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-6 max-[740px]:p-4">
      <h2 className="mt-0 text-lg font-semibold">网络测试</h2>
      <p className="text-sm leading-6 text-slate-500">
        使用设备 SIM 卡的 4G 网络发送 GET 请求，检查连接和响应。请求前 1024
        字节，响应正文不展示，测试后清理。
      </p>
      {!ready ? (
        <Alert className="mb-4!" type="info" showIcon title="请先连接设备" />
      ) : !supported ? (
        <Alert
          className="mb-4!"
          type="warning"
          showIcon
          title="需要更新设备 Lua 脚本以启用网络测试"
        />
      ) : null}
      <Form
        layout="vertical"
        initialValues={{ url: 'https://www.baidu.com' }}
        onFinish={({ url }: { url: string }) => test.mutate(url)}
      >
        <Form.Item
          label="测试地址"
          name="url"
          rules={[
            { required: true, message: '请输入测试地址' },
            { type: 'url', message: '请输入完整的 HTTP/HTTPS 地址' },
          ]}
        >
          <Input
            aria-label="网络测试地址"
            placeholder="https://www.baidu.com"
            maxLength={1024}
            disabled={test.isPending}
          />
        </Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          icon={<GlobalOutlined />}
          loading={test.isPending}
          disabled={!ready || !supported}
        >
          开始测试
        </Button>
      </Form>
      {test.isPending && (
        <p role="status" className="text-sm text-slate-500">
          设备正在连接目标，最多等待 25 秒…
        </p>
      )}
      {test.isError && <Alert className="mt-4!" type="error" showIcon title={test.error.message} />}
      {result && !test.isPending && !test.isError && (
        <div className="mt-5 space-y-3 break-all rounded-xl bg-slate-50 p-4" aria-live="polite">
          <Tag color={result.reachable ? 'green' : 'red'}>
            {result.reachable ? '已收到 HTTP 响应' : '网络访问失败'}
          </Tag>
          <p className="m-0 text-sm">
            设备 4G · {result.method} · {result.url}
          </p>
          <p className="m-0 text-sm">
            响应状态：{result.status_code ?? '—'} · 耗时：{result.elapsed_ms} ms
          </p>
          {result.error && <p className="m-0 text-sm text-red-600">{result.error}</p>}
          {Object.entries(result.headers ?? {}).map(([key, value]) => (
            <p key={key} className="m-0 text-xs text-slate-500">
              {key}: {value}
            </p>
          ))}
          <p className="m-0 text-xs text-slate-500">
            HTTP 4xx/5xx 表示服务器已响应；负数为设备请求错误码。服务器可能忽略字节范围请求。
          </p>
        </div>
      )}
    </section>
  )
}
