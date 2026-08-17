import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// 诊断：把任何未捕获错误直接显示到页面，便于定位白屏问题
function showError(msg: string) {
  const el = document.createElement('pre')
  el.style.cssText =
    'position:fixed;top:0;left:0;right:0;background:#fdd;color:#900;padding:12px;z-index:99999;white-space:pre-wrap;font-size:12px;margin:0;max-height:60vh;overflow:auto'
  el.textContent = msg
  document.body.appendChild(el)
}

window.addEventListener('error', (e) => {
  showError('window.onerror: ' + e.message + '\n' + (e.error?.stack ?? ''))
})
window.addEventListener('unhandledrejection', (e) => {
  showError('unhandledrejection: ' + String(e.reason))
})

const rootEl = document.getElementById('root')
if (!rootEl) {
  showError('root element missing')
} else {
  try {
    // 不使用 StrictMode：dev 模式双重挂载会触发 echarts 二次初始化问题
    ReactDOM.createRoot(rootEl).render(<App />)
  } catch (err) {
    const e = err as Error
    showError('render error: ' + e.message + '\n' + (e.stack ?? ''))
  }
}
