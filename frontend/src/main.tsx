import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { createRootRoute, createRoute, createRouter, RouterProvider } from '@tanstack/react-router'
import { ConfigProvider, App as AntApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { queryClient } from './connection'
import Console from './Console'
import './tailwind.css'
const root = createRootRoute()
const index = createRoute({ getParentRoute: () => root, path: '/', component: Console })
const router = createRouter({ routeTree: root.addChildren([index]) })
declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: '#1766c2',
            colorText: '#163b5c',
            colorBgLayout: '#eef3f8',
            borderRadius: 12,
            fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
          },
        }}
      >
        <AntApp className="min-h-screen bg-[#eef3f8] font-sans text-[#163b5c]">
          <RouterProvider router={router} />
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
