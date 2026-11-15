import React, { useEffect, useState } from 'react'
import {
  Layout, Typography, Tabs, Input, Select, Button, Table, Card, Alert, Spin,
  List, Badge, Tag, Space, Popconfirm, message,
} from 'antd'
import { SearchOutlined, DownloadOutlined, FireOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { getJSON, postJSON, downloadMusic, getUserCookie, setUserCookie } from './api.js'

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

function CookieCard() {
  const [value, setValue] = useState(getUserCookie())
  return (
    <Card size="small" title="个人 Cookie（可选，高音质下载用）" style={{ marginBottom: 16 }}>
      <Space.Compact style={{ width: '100%' }}>
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
      <Text type="secondary" style={{ fontSize: 12 }}>
        仅保存在本浏览器 localStorage 并随下载请求发送，服务器不做任何持久化。
      </Text>
    </Card>
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
    { title: '歌曲', dataIndex: 'name', key: 'name' },
    { title: '歌手', dataIndex: 'artists', key: 'artists' },
    { title: '专辑', dataIndex: 'album', key: 'album' },
    {
      title: '操作', key: 'actions', width: 200,
      render: (_, record) => (
        <Space>
          <Button size="small" type="link" onClick={() => onUseSong(String(record.id))}>去解析</Button>
          <Button size="small" type="link" icon={<DownloadOutlined />}
                  onClick={() => onUseSong(String(record.id), true)}>去下载</Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Space.Compact style={{ width: '70%' }}>
        <Input value={keyword} placeholder="输入歌曲名 / 歌手 / 专辑关键词"
               onChange={(e) => setKeyword(e.target.value)}
               onPressEnter={doSearch} prefix={<SearchOutlined />} />
        <Button type="primary" loading={loading} onClick={doSearch}>搜索</Button>
      </Space.Compact>
      <Table style={{ marginTop: 16 }} size="small" rowKey="id"
             columns={columns} dataSource={rows}
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
      <Space.Compact style={{ width: '70%' }}>
        <Input value={input} placeholder="输入音乐 ID 或网易云音乐歌曲链接"
               onChange={(e) => setInput(e.target.value)} onPressEnter={doParse} />
        <Button type="primary" loading={loading} onClick={doParse}>解析</Button>
      </Space.Compact>
      {loading && <Spin style={{ marginTop: 16 }} />}
      {info && (
        <Card size="small" style={{ marginTop: 16 }} title={`${info.name || '未知歌曲'} - ${info.ar_name || '未知歌手'}`}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Space wrap>
              <Tag color="blue">专辑：{info.al_name || '未知'}</Tag>
              <Tag color="green">音质：{info.level}</Tag>
              {info.size && <Tag color="orange">大小：{info.size}</Tag>}
            </Space>
            {info.url ? (
              <audio controls src={info.url} style={{ width: '100%' }} />
            ) : (
              <Alert type="warning" showIcon message="未获取到试听/下载链接（可能版权限制或音质不支持）" />
            )}
            <Paragraph>
              <a href={info.url || '#'} target="_blank" rel="noreferrer"
                 aria-disabled={!info.url}>直接打开音频链接</a>
            </Paragraph>
            {info.lyric && <div className="lyric-box">{info.lyric}</div>}
          </Space>
        </Card>
      )}
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
    { title: '歌曲', dataIndex: 'name', key: 'name' },
    { title: '歌手', dataIndex: 'artists', key: 'artists' },
    { title: '专辑', dataIndex: 'album', key: 'album' },
  ]

  return (
    <div>
      <Space.Compact style={{ width: '70%' }}>
        <Input value={input} placeholder="输入歌单 ID 或歌单链接"
               onChange={(e) => setInput(e.target.value)} onPressEnter={doParse} />
        <Button type="primary" loading={loading} onClick={doParse}>解析</Button>
      </Space.Compact>
      {data && (
        <div style={{ marginTop: 16 }}>
          <Title level={5}>{data.name || '未命名歌单'}（{tracks.length} 首）</Title>
          <Table size="small" rowKey="id" columns={columns} dataSource={tracks}
                 pagination={{ pageSize: 10 }} />
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

  const tracks = Array.isArray(data?.tracks) ? data.tracks : []
  const columns = [
    { title: '歌曲', dataIndex: 'name', key: 'name' },
    { title: '歌手', dataIndex: 'artists', key: 'artists' },
  ]

  return (
    <div>
      <Space.Compact style={{ width: '70%' }}>
        <Input value={input} placeholder="输入专辑 ID 或专辑链接"
               onChange={(e) => setInput(e.target.value)} onPressEnter={doParse} />
        <Button type="primary" loading={loading} onClick={doParse}>解析</Button>
      </Space.Compact>
      {data && (
        <div style={{ marginTop: 16 }}>
          <Title level={5}>{data.name || '未命名专辑'}</Title>
          {data.description && (
            <Paragraph type="secondary" ellipsis={{ rows: 3, expandable: true }}>
              {data.description}
            </Paragraph>
          )}
          <Table size="small" rowKey="id" columns={columns} dataSource={tracks}
                 pagination={{ pageSize: 10 }} />
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
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space.Compact style={{ width: '70%' }}>
          <Input value={input} placeholder="输入音乐 ID 或网易云音乐歌曲链接"
                 onChange={(e) => setInput(e.target.value)} />
          <Select value={quality} onChange={setQuality} style={{ width: 160 }}
                  options={QUALITIES} />
          <Button type="primary" icon={<DownloadOutlined />} loading={loading} onClick={doDownload}>
            下载音乐
          </Button>
        </Space.Compact>
        <Alert type="info" showIcon
               message="浏览器将按服务端实际音频格式命名保存（如 歌名.mp3 / 歌名.flac）；已缓存文件直接复用并计入排行榜。" />
        {recs.length > 0 && (
          <Card size="small" title={<span><ThunderboltOutlined /> 智能推荐（基于本次下载，每次 5 首）</span>}>
            <List size="small" dataSource={recs} renderItem={(it) => (
              <List.Item actions={[
                <Button size="small" type="link" onClick={() => setInput(String(it.song_id))}>填入下载</Button>,
              ]}>
                <List.Item.Meta
                  title={it.name}
                  description={
                    <Space wrap size={4}>
                      <Text type="secondary">{it.artists}</Text>
                      {(it.qualities || []).map((q) => (
                        <Tag key={q.quality}>
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
      </Space>
    </div>
  )
}

function RankCard() {
  const [items, setItems] = useState([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    getJSON('/rank')
      .then((body) => setItems(body.data?.rank || []))
      .catch(() => setItems([]))
      .finally(() => setLoaded(true))
  }, [])

  return (
    <Card size="small" title={<span><FireOutlined style={{ color: '#c20c0c' }} /> 下载排行榜</span>}
          style={{ marginBottom: 16 }}>
      {!loaded ? <Spin size="small" /> : items.length === 0 ? (
        <Text type="secondary">暂无下载记录，完成首次下载后这里会出现热门歌曲。</Text>
      ) : (
        <List size="small" dataSource={items} renderItem={(it, idx) => (
          <List.Item>
            <Space>
              <Badge count={idx + 1} style={{ backgroundColor: idx < 3 ? '#c20c0c' : '#999' }} />
              <Text>{it.name}</Text>
              <Text type="secondary">{it.artists}</Text>
            </Space>
            <Tag color="red">{it.session_count} 人下载</Tag>
          </List.Item>
        )} />
      )}
    </Card>
  )
}

export default function App() {
  const [tab, setTab] = useState('search')
  const [preset, setPreset] = useState(null)

  // 从搜索/解析跳到下载页并预填 ID
  const useSong = (id, jump = false) => {
    setPreset({ id, ts: Date.now() })
    if (jump) setTab('download')
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ background: '#c20c0c' }}>
        <Title level={3} style={{ color: '#fff', margin: 0, lineHeight: '64px' }}>
          网易云音乐工具箱
        </Title>
      </Header>
      <Content style={{ padding: 24, maxWidth: 1100, margin: '0 auto', width: '100%' }}>
        <CookieCard />
        <div className="page-card">
          <Tabs activeKey={tab} onChange={setTab}
                items={[
                  { key: 'search', label: '歌曲搜索', children: <SearchTab onUseSong={useSong} /> },
                  { key: 'song', label: '单曲解析', children: <SongParseTab /> },
                  { key: 'playlist', label: '歌单解析', children: <PlaylistTab /> },
                  { key: 'album', label: '专辑解析', children: <AlbumTab /> },
                  { key: 'download', label: '音乐下载', children: <DownloadTab preset={preset} /> },
                ]} />
        </div>
        <div style={{ marginTop: 16 }}>
          <RankCard />
        </div>
      </Content>
      <Footer style={{ textAlign: 'center', color: '#999' }}>
        网易云音乐工具箱 · 仅供个人学习研究，请支持正版音乐
      </Footer>
    </Layout>
  )
}
