import React, { useEffect, useState } from 'react'
import {
  Layout, Typography, Tabs, Input, Select, Button, Table, Card, Alert, Skeleton,
  List, Tag, Space, message,
} from 'antd'
import {
  SearchOutlined, DownloadOutlined, ThunderboltOutlined, DownOutlined,
  KeyOutlined, CustomerServiceOutlined, InfoCircleOutlined,
} from '@ant-design/icons'
import { getJSON, postJSON, downloadMusic, downloadBatch, getUserCookie, setUserCookie } from './api.js'
import musicLogo from './assets/music.svg'

const { Header, Content, Footer } = Layout
const { Title, Text, Paragraph } = Typography

const QUALITIES = [
  { value: 'standard', label: '标准音质' },
  { value: 'exhigh', label: '极高音质' },
  { value: 'lossless', label: '无损音质' },
  { value: 'hires', label: 'Hi-Res 音质' },
  { value: 'sky', label: '沉浸环绕声' },
  { value: 'jyeffect', label: '高清环绕声' },
  { value: 'jymaster', label: '超清母带' },
  { value: 'dolby', label: '杜比全景声' },
]

// 提取网易云链接中的 ID（与后端解析口径一致的前端预处理）
function extractId(input) {
  const m = String(input || '').match(/song\?id=(\d+)/)
  if (m) return m[1]
  const m2 = String(input || '').match(/playlist\?id=(\d+)/)
  if (m2) return `playlist:${m2[1]}`
  const m3 = String(input || '').match(/album\?id=(\d+)/)
  if (m3) return `album:${m3[1]}`
  return String(input || '').trim()
}

// 毫秒时间戳 → YYYY-MM-DD（仅用于专辑发行时间展示）
function formatDate(ms) {
  if (!ms) return ''
  const d = new Date(Number(ms))
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// 封面缺失时的占位块
function CoverPlaceholder({ className }) {
  return <div className={`album-cover placeholder ${className || ''}`}><CustomerServiceOutlined /></div>
}

// 个人 Cookie：紧凑可折叠设置模块（凭据仅存 localStorage，逻辑与原 CookieCard 一致）
function CookieSetting({ open, onToggle }) {
  const [value, setValue] = useState(getUserCookie())
  const configured = value.trim().length > 0
  return (
    <section className={`cookie-panel${open ? ' open' : ''}`}>
      <button type="button" className="cookie-summary" onClick={onToggle} aria-expanded={open}>
        <span className="cookie-title">个人 Cookie</span>
        <span className={`cookie-state${configured ? ' ok' : ''}`}>{configured ? '已配置' : '未配置'}</span>
        <span className="cookie-hint">可选 · 用于高音质下载 · 仅保存在本浏览器</span>
        <DownOutlined className="cookie-chevron" />
      </button>
      {open && (
        <div className="cookie-body">
          <Space.Compact style={{ width: '100%' }} className="cookie-input">
            <Input.Password
              value={value}
              placeholder="粘贴你的网易云音乐 Cookie（k1=v1; k2=v2 格式）"
              autoComplete="off"
              onChange={(e) => {
                setValue(e.target.value)
                setUserCookie(e.target.value)
              }}
            />
            <Button onClick={() => { setValue(''); setUserCookie('') }}>清除</Button>
          </Space.Compact>
          <Text type="secondary" className="hint-line">
            仅保存在本浏览器 localStorage 并随下载请求发送，服务器不做任何持久化。
          </Text>
        </div>
      )}
    </section>
  )
}

function SearchTab({ onUseSong }) {
  const [keyword, setKeyword] = useState('')
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState([])

  const doSearch = async () => {
    if (!keyword.trim()) { message.warning('请输入搜索关键词'); return }
    setLoading(true)
    try {
      const body = await postJSON('/search', { keyword: keyword.trim(), limit: 30 })
      setRows(body.data || [])
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const columns = [
    { title: '#', key: 'index', width: 56, render: (_, __, idx) => <span className="cell-index">{idx + 1}</span> },
    { title: '歌曲', dataIndex: 'name', key: 'name', render: (v) => <span className="cell-name">{v}</span> },
    { title: '歌手', dataIndex: 'artists', key: 'artists', render: (v) => <span className="cell-sub">{v}</span> },
    { title: '专辑', dataIndex: 'album', key: 'album', render: (v) => <span className="cell-sub">{v}</span> },
    {
      title: '操作', key: 'actions', width: 150, align: 'right',
      render: (_, record) => (
        <Space size={4}>
          <Button size="small" type="link" onClick={() => onUseSong(String(record.id))}>去解析</Button>
          <Button size="small" type="link" icon={<DownloadOutlined />}
                  onClick={() => onUseSong(String(record.id), true)}>去下载</Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <p className="tab-desc">输入关键词检索曲库，支持歌曲名 / 歌手 / 专辑，可直接跳转解析或下载</p>
      <Space.Compact className="action-compact">
        <Input size="large" value={keyword} placeholder="输入歌曲名 / 歌手 / 专辑关键词"
               onChange={(e) => setKeyword(e.target.value)}
               onPressEnter={doSearch} prefix={<SearchOutlined className="input-icon" />} />
        <Button size="large" type="primary" loading={loading} onClick={doSearch}>搜索</Button>
      </Space.Compact>
      <Table className="data-table" style={{ marginTop: 16 }} size="middle" rowKey="id"
             columns={columns} dataSource={rows} loading={loading} scroll={{ x: 680 }}
             pagination={{ pageSize: 10, showSizeChanger: false }}
             locale={{ emptyText: '输入关键词开始搜索' }} />
    </div>
  )
}

function SongParseTab() {
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [info, setInfo] = useState(null)

  const doParse = async () => {
    const id = extractId(input)
    if (!id) { message.warning('请输入音乐 ID 或链接'); return }
    setLoading(true)
    setInfo(null)
    try {
      const body = await postJSON('/song', { id, level: 'lossless', type: 'json' })
      setInfo(body.data)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <p className="tab-desc">解析单曲信息：封面、试听、歌词与直链，支持音乐 ID 或完整歌曲链接</p>
      <Space.Compact className="action-compact">
        <Input size="large" value={input} placeholder="输入音乐 ID 或网易云音乐歌曲链接"
               onChange={(e) => setInput(e.target.value)} onPressEnter={doParse} />
        <Button size="large" type="primary" loading={loading} onClick={doParse}>解析</Button>
      </Space.Compact>
      {loading && (
        <Skeleton className="song-skeleton" active
                  avatar={{ shape: 'square', size: 112 }} paragraph={{ rows: 3 }} />
      )}
      {info && (
        <div className="result">
          <div className="album-head">
            {info.pic
              ? <img className="album-cover sm" src={info.pic} alt={info.name || '歌曲封面'} />
              : <CoverPlaceholder className="sm" />}
            <div className="album-info">
              <Title level={4} className="result-title">{info.name || '未知歌曲'}</Title>
              <Text type="secondary" className="result-sub">{info.ar_name || '未知歌手'}</Text>
              <Space wrap size={6} className="tag-row">
                <Tag className="tag-soft">专辑：{info.al_name || '未知'}</Tag>
                <Tag className="tag-brand">音质：{info.level}</Tag>
                {info.size && <Tag className="tag-soft">大小：{info.size}</Tag>}
              </Space>
              {info.url ? (
                <audio controls src={info.url} className="audio-player" />
              ) : (
                <Alert type="warning" showIcon
                       message="未获取到试听/下载链接（可能版权限制或音质不支持）" />
              )}
              <a className="direct-link" href={info.url || '#'} target="_blank" rel="noreferrer"
                 aria-disabled={!info.url}>直接打开音频链接</a>
              {info.lyric && <div className="lyric-box">{info.lyric}</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// 批量下载操作条：服务端逐首下载并打包 zip（等待时长随曲目数与音质增加）
function BatchDownloadBar({ tracks, batchName }) {
  const [quality, setQuality] = useState('lossless')
  const [loading, setLoading] = useState(false)

  const doBatch = async () => {
    if (!tracks.length) return
    setLoading(true)
    message.loading({ content: `服务端正在下载 ${tracks.length} 首歌曲并打包，请勿关闭页面…`, key: 'batch', duration: 0 })
    try {
      const { filename, total, failed } = await downloadBatch({
        tracks: tracks.map((t) => ({ id: String(t.id), name: t.name, artists: t.artists })),
        quality,
        name: batchName,
        userCookie: getUserCookie(),
      })
      message.success({
        content: failed > 0
          ? `批量完成：${filename}（成功 ${total - failed}/${total} 首，${failed} 首因版权或 Cookie 限制跳过）`
          : `批量完成：${filename}（${total} 首全部成功）`,
        key: 'batch',
        duration: 6,
      })
    } catch (e) {
      message.error({ content: e.message, key: 'batch', duration: 6 })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="batch-bar">
      <Select value={quality} onChange={setQuality} style={{ width: 150 }} options={QUALITIES} />
      <Button icon={<DownloadOutlined />} loading={loading} onClick={doBatch}>
        批量下载（{tracks.length} 首）
      </Button>
      <Text type="secondary" className="hint-line">服务端打包 zip，单批最多 100 首</Text>
    </div>
  )
}

// 解析结果头：封面 + 名称 + 元信息 + 简介限行（歌单 / 专辑共用布局）
function ResultHead({ cover, name, fallbackName, children }) {
  return (
    <div className="album-head">
      {cover
        ? <img className="album-cover" src={cover} alt={name || fallbackName} />
        : <CoverPlaceholder />}
      <div className="album-info">
        <Title level={4} className="result-title">{name || fallbackName}</Title>
        {children}
      </div>
    </div>
  )
}

function PlaylistTab() {
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)

  const doParse = async () => {
    let id = extractId(input)
    if (id.startsWith('playlist:')) id = id.slice(9)
    if (!id || id.includes(':')) { message.warning('请输入歌单 ID 或歌单链接'); return }
    setLoading(true)
    setData(null)
    try {
      const body = await getJSON('/playlist', { id })
      setData(body.data?.playlist || null)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const tracks = Array.isArray(data?.tracks) ? data.tracks : []
  const columns = [
    { title: '#', key: 'index', width: 56, render: (_, __, idx) => <span className="cell-index">{idx + 1}</span> },
    { title: '歌曲', dataIndex: 'name', key: 'name', render: (v) => <span className="cell-name">{v}</span> },
    { title: '歌手', dataIndex: 'artists', key: 'artists', render: (v) => <span className="cell-sub">{v}</span> },
    { title: '专辑', dataIndex: 'album', key: 'album', render: (v) => <span className="cell-sub">{v}</span> },
  ]

  return (
    <div>
      <p className="tab-desc">解析歌单详情与全部曲目，支持批量打包下载，支持歌单 ID 或完整链接</p>
      <Space.Compact className="action-compact">
        <Input size="large" value={input} placeholder="输入歌单 ID 或歌单链接"
               onChange={(e) => setInput(e.target.value)} onPressEnter={doParse} />
        <Button size="large" type="primary" loading={loading} onClick={doParse}>解析</Button>
      </Space.Compact>
      {loading && (
        <Skeleton className="song-skeleton" active
                  avatar={{ shape: 'square', size: 112 }} paragraph={{ rows: 3 }} />
      )}
      {data && (
        <div className="result">
          <ResultHead cover={data.coverImgUrl} name={data.name} fallbackName="未命名歌单">
            <div className="album-meta">
              {data.creator && <span>{data.creator} 创建</span>}
              <span className="meta-dot" />
              <span>{tracks.length} 首</span>
            </div>
            {data.description && (
              <Paragraph type="secondary" className="result-desc"
                         ellipsis={{ rows: 2, expandable: true }}>
                {data.description}
              </Paragraph>
            )}
          </ResultHead>
          <Table className="data-table" size="middle" rowKey="id" columns={columns}
                 dataSource={tracks} scroll={{ x: 680 }} pagination={{ pageSize: 10 }} />
          {tracks.length > 0 && <BatchDownloadBar tracks={tracks} batchName={data.name} />}
        </div>
      )}
    </div>
  )
}

function AlbumTab() {
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)

  const doParse = async () => {
    let id = extractId(input)
    if (id.startsWith('album:')) id = id.slice(6)
    if (!id || id.includes(':')) { message.warning('请输入专辑 ID 或专辑链接'); return }
    setLoading(true)
    setData(null)
    try {
      const body = await getJSON('/album', { id })
      setData(body.data?.album || null)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  // 后端 /album 返回的曲目字段名为 songs（music_api.get_album_detail）
  const tracks = Array.isArray(data?.songs) ? data.songs : []
  const publishDate = formatDate(data?.publishTime)
  const columns = [
    { title: '#', key: 'index', width: 56, render: (_, __, idx) => <span className="cell-index">{idx + 1}</span> },
    { title: '歌曲', dataIndex: 'name', key: 'name', render: (v) => <span className="cell-name">{v}</span> },
    { title: '歌手', dataIndex: 'artists', key: 'artists', render: (v) => <span className="cell-sub">{v}</span> },
  ]

  return (
    <div>
      <p className="tab-desc">解析专辑详情与完整曲目列表，支持批量打包下载，支持专辑 ID 或完整链接</p>
      <Space.Compact className="action-compact">
        <Input size="large" value={input} placeholder="输入专辑 ID 或专辑链接"
               onChange={(e) => setInput(e.target.value)} onPressEnter={doParse} />
        <Button size="large" type="primary" loading={loading} onClick={doParse}>解析</Button>
      </Space.Compact>
      {loading && (
        <Skeleton className="song-skeleton" active
                  avatar={{ shape: 'square', size: 112 }} paragraph={{ rows: 3 }} />
      )}
      {data && (
        <div className="result">
          <ResultHead cover={data.coverImgUrl} name={data.name} fallbackName="未命名专辑">
            <div className="album-meta">
              {data.artist && <span>{data.artist}</span>}
              {publishDate && (<><span className="meta-dot" /><span>发行于 {publishDate}</span></>)}
              <span className="meta-dot" />
              <span>{tracks.length} 首</span>
            </div>
            {data.description && (
              <Paragraph type="secondary" className="result-desc"
                         ellipsis={{ rows: 2, expandable: true }}>
                {data.description}
              </Paragraph>
            )}
          </ResultHead>
          <Table className="data-table" size="middle" rowKey="id" columns={columns}
                 dataSource={tracks} scroll={{ x: 560 }} pagination={{ pageSize: 10 }} />
          {tracks.length > 0 && <BatchDownloadBar tracks={tracks} batchName={data.name} />}
        </div>
      )}
    </div>
  )
}

function DownloadTab({ preset }) {
  const [input, setInput] = useState(preset?.id || '')
  const [quality, setQuality] = useState('lossless')
  const [loading, setLoading] = useState(false)
  const [recs, setRecs] = useState([])

  useEffect(() => {
    if (preset?.id) setInput(preset.id)
  }, [preset])

  const loadRecommendations = async (songId) => {
    if (!songId) return
    try {
      const body = await getJSON('/recommend', { song_id: songId })
      setRecs(body.data?.recommendations || [])
    } catch (_) {
      setRecs([])
    }
  }

  const doDownload = async () => {
    const id = extractId(input)
    if (!id || id.includes(':')) { message.warning('请输入音乐 ID 或歌曲链接'); return }
    setLoading(true)
    try {
      const { filename } = await downloadMusic({
        id, quality, userCookie: getUserCookie(),
      })
      message.success(`下载完成：${filename}（已按实际音频格式命名保存）`)
      loadRecommendations(String(parseInt(id, 10) || id))
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <p className="tab-desc">直接下载单曲并按实际音频格式保存，可选音质；下载成功后推荐相似音乐</p>
      <Space.Compact className="action-compact">
        <Input size="large" value={input} placeholder="输入音乐 ID 或网易云音乐歌曲链接"
               onChange={(e) => setInput(e.target.value)} />
        <Select size="large" value={quality} onChange={setQuality} style={{ width: 160 }}
                options={QUALITIES} />
        <Button size="large" type="primary" icon={<DownloadOutlined />} loading={loading} onClick={doDownload}>
          下载音乐
        </Button>
      </Space.Compact>
      <p className="hint-line">
        <InfoCircleOutlined /> 浏览器将按服务端实际音频格式命名保存（如 歌名.mp3 / 歌名.flac）；已缓存文件直接复用并计入排行榜。
      </p>
      {recs.length > 0 && (
        <Card className="rank-card in-tab" size="small"
              title={<span><ThunderboltOutlined className="title-icon" /> 智能推荐（基于本次下载，每次 5 首）</span>}>
          <List size="small" dataSource={recs} renderItem={(it) => (
            <List.Item actions={[
              <Button size="small" type="link" onClick={() => setInput(String(it.song_id))}>填入下载</Button>,
            ]}>
              <List.Item.Meta
                title={<span className="cell-name">{it.name}</span>}
                description={
                  <Space wrap size={4}>
                    <Text type="secondary">{it.artists}</Text>
                    {(it.qualities || []).map((q) => (
                      <Tag key={q.quality} className="tag-soft">
                        {q.quality}{q.size ? ` · ${(q.size / 1048576).toFixed(2)}MB` : ''}
                      </Tag>
                    ))}
                  </Space>
                }
              />
            </List.Item>
          )} />
        </Card>
      )}
    </div>
  )
}

function RankCard() {
  const [items, setItems] = useState([])
  const [days, setDays] = useState(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    getJSON('/rank')
      .then((body) => {
        setItems(body.data?.rank || [])
        setDays(body.data?.window_days ?? null)
      })
      .catch(() => setItems([]))
      .finally(() => setLoaded(true))
  }, [])

  return (
    <Card className="rank-card" size="small"
          title={`下载排行榜${days ? ` · 近 ${days} 天` : ''}`}>
      {!loaded ? (
        <Skeleton active title={false} paragraph={{ rows: 4 }} />
      ) : items.length === 0 ? (
        <Text type="secondary">暂无下载记录，完成首次下载后这里会出现热门歌曲。</Text>
      ) : (
        <List size="small" dataSource={items} renderItem={(it, idx) => (
          <List.Item className="rank-item">
            <div className="rank-main">
              <span className={`rank-no${idx < 3 ? ' top' : ''}`}>{idx + 1}</span>
              <span className="rank-name">{it.name}</span>
              <span className="rank-artist">{it.artists}</span>
            </div>
            <span className="rank-count">{it.session_count} 人下载</span>
          </List.Item>
        )} />
      )}
    </Card>
  )
}

export default function App() {
  const [tab, setTab] = useState('search')
  const [preset, setPreset] = useState(null)
  const [cookieOpen, setCookieOpen] = useState(false)

  // 从搜索/解析跳到下载页并预填 ID
  const useSong = (id, jump = false) => {
    setPreset({ id, ts: Date.now() })
    if (jump) setTab('download')
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header className="site-header">
        <div className="header-inner">
          <div className="brand">
            <img className="brand-logo" src={musicLogo} alt="网易云音乐工具箱" />
            <div className="brand-text">
              <span className="brand-title">网易云音乐工具箱</span>
              <span className="brand-sub">搜索 · 解析 · 下载</span>
            </div>
          </div>
          <Button type="text" className="header-action" icon={<KeyOutlined />}
                  onClick={() => setCookieOpen((v) => !v)}>
            Cookie 设置
          </Button>
        </div>
      </Header>
      <Content className="site-content">
        <div className="container">
          <CookieSetting open={cookieOpen} onToggle={() => setCookieOpen((v) => !v)} />
          <section className="main-panel">
            <Tabs activeKey={tab} onChange={setTab}
                  items={[
                    { key: 'search', label: '歌曲搜索', children: <SearchTab onUseSong={useSong} /> },
                    { key: 'song', label: '单曲解析', children: <SongParseTab /> },
                    { key: 'playlist', label: '歌单解析', children: <PlaylistTab /> },
                    { key: 'album', label: '专辑解析', children: <AlbumTab /> },
                    { key: 'download', label: '音乐下载', children: <DownloadTab preset={preset} /> },
                  ]} />
          </section>
          <RankCard />
        </div>
      </Content>
      <Footer className="site-footer">
        网易云音乐工具箱 · 仅供个人学习研究，请支持正版音乐
      </Footer>
    </Layout>
  )
}
