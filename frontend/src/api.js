// 统一 API 封装：JSON 请求/响应 + 统一错误提示
// 约定：服务端响应结构 {status, success, message, data?}

async function request(path, options = {}) {
  let resp
  try {
    resp = await fetch(path, options)
  } catch (e) {
    throw new Error('网络请求失败，请确认服务已启动')
  }
  const ctype = resp.headers.get('content-type') || ''
  if (!ctype.includes('application/json')) {
    // 非预期响应（如静态页兜底），统一报错
    throw new Error(`服务返回异常（HTTP ${resp.status}）`)
  }
  const body = await resp.json()
  if (!body.success) {
    throw new Error(body.message || `请求失败（HTTP ${resp.status}）`)
  }
  return body
}

export async function getJSON(path, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
  ).toString()
  return request(qs ? `${path}?${qs}` : path)
}

export async function postJSON(path, data = {}) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

// 下载音乐：XHR blob + 服务端响应头命名，浏览器按实际音频格式保存
export function downloadMusic({ id, quality, userCookie }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/download', true)
    xhr.setRequestHeader('Content-Type', 'application/json')
    xhr.responseType = 'blob'
    xhr.onload = () => {
      if (xhr.status !== 200) {
        // 错误体是 JSON
        try {
          const body = JSON.parse(xhr.responseText)
          reject(new Error(body.message || `下载失败（HTTP ${xhr.status}）`))
        } catch (_) {
          reject(new Error(`下载失败（HTTP ${xhr.status}）`))
        }
        return
      }
      const encoded = xhr.getResponseHeader('X-Download-Filename')
      const filename = encoded ? decodeURIComponent(encoded) : 'music.mp3'
      const url = window.URL.createObjectURL(xhr.response)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
      resolve({ filename })
    }
    xhr.onerror = () => reject(new Error('网络错误，下载失败'))
    xhr.send(JSON.stringify({ id, quality, user_cookie: userCookie || '' }))
  })
}

// 批量下载：服务端逐首下载并打包 zip（XHR blob 复用单曲下载的保存体验）
export function downloadBatch({ tracks, quality, name, userCookie }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/batch_download', true)
    xhr.setRequestHeader('Content-Type', 'application/json')
    xhr.responseType = 'blob'
    xhr.onload = () => {
      if (xhr.status !== 200) {
        // 错误体是 JSON
        try {
          const body = JSON.parse(xhr.responseText)
          reject(new Error(body.message || `批量下载失败（HTTP ${xhr.status}）`))
        } catch (_) {
          reject(new Error(`批量下载失败（HTTP ${xhr.status}）`))
        }
        return
      }
      const encoded = xhr.getResponseHeader('X-Download-Filename')
      const filename = encoded ? decodeURIComponent(encoded) : 'batch.zip'
      const num = (key) => Number(xhr.getResponseHeader(key) ?? 0)
      const url = window.URL.createObjectURL(xhr.response)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
      resolve({ filename, total: num('X-Batch-Total'), success: num('X-Batch-Success'), failed: num('X-Batch-Failed') })
    }
    xhr.onerror = () => reject(new Error('网络错误，批量下载失败'))
    xhr.send(JSON.stringify({ tracks, quality, name, user_cookie: userCookie || '' }))
  })
}

// 个人 Cookie：仅存于浏览器 localStorage，随下载请求发送（服务器不持久化）
const USER_COOKIE_KEY = 'netease_user_cookie'

export function getUserCookie() {
  return localStorage.getItem(USER_COOKIE_KEY) || ''
}

export function setUserCookie(value) {
  const v = (value || '').trim()
  if (v) {
    localStorage.setItem(USER_COOKIE_KEY, v)
  } else {
    localStorage.removeItem(USER_COOKIE_KEY)
  }
}
