import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App.jsx'
import './styles.css'

// 全局设计令牌：品牌红仅作强调色，整体走浅灰底 + 纯白容器 + 细腻边框的轻量风格
const theme = {
  token: {
    colorPrimary: '#c20c0c',
    colorInfo: '#c20c0c',
    colorLink: '#c20c0c',
    colorBgLayout: '#f6f6f7',
    colorBgContainer: '#ffffff',
    colorText: '#1a1a1e',
    colorTextSecondary: '#5c5c63',
    colorTextTertiary: '#9a9aa2',
    colorTextQuaternary: '#c3c3ca',
    colorBorder: '#d9d9de',
    colorBorderSecondary: '#ececf0',
    borderRadius: 10,
    controlHeight: 36,
    fontSize: 14,
    fontFamily:
      "-apple-system, 'SF Pro Text', 'PingFang SC', 'Segoe UI', 'Helvetica Neue', 'Microsoft YaHei', sans-serif",
    fontWeightStrong: 600,
    controlOutline: 'rgba(194, 12, 12, 0.10)',
    controlOutlineWidth: 3,
    boxShadow: '0 1px 2px rgba(15, 15, 20, 0.04)',
    boxShadowSecondary: '0 4px 16px rgba(15, 15, 20, 0.08)',
  },
  components: {
    Layout: {
      headerBg: '#ffffff',
      headerHeight: 60,
      headerPadding: '0 24px',
      bodyBg: '#f6f6f7',
      footerBg: 'transparent',
      footerPadding: '20px 24px',
    },
    Card: {
      borderRadiusLG: 14,
      headerFontSize: 14,
      headerFontSizeSM: 14,
      bodyPaddingSM: 16,
    },
    Tabs: {
      horizontalMargin: '0 0 16px 0',
      horizontalItemGutter: 24,
      horizontalItemPadding: '12px 2px',
      itemColor: '#5c5c63',
      itemHoverColor: '#1a1a1e',
      itemSelectedColor: '#1a1a1e',
      inkBarColor: '#c20c0c',
      titleFontSize: 14,
    },
    Table: {
      headerBg: 'transparent',
      headerColor: '#9a9aa2',
      headerSplitColor: 'transparent',
      rowHoverBg: '#fafafa',
      cellPaddingBlock: 12,
      cellPaddingInline: 12,
      borderColor: '#f2f2f4',
    },
    Button: {
      fontWeight: 500,
      primaryShadow: '0 1px 2px rgba(194, 12, 12, 0.16)',
      defaultShadow: 'none',
      dangerShadow: 'none',
    },
    Input: {
      activeShadow: '0 0 0 3px rgba(194, 12, 12, 0.08)',
    },
  },
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <ConfigProvider locale={zhCN} theme={theme}>
    <App />
  </ConfigProvider>,
)
