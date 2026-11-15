"""批量下载路由自检（pytest 管理）

覆盖范围（全部不发起真实上游下载）：
1. 参数校验分支：tracks 缺失/超上限/非对象项/非法 ID/非法音质 → 400 统一结构
2. 打包链路：monkeypatch 下载器为本地假文件（含单首抛 DownloadException）→
   断言 zip 结构、条目命名、X-Batch-* / X-Download-Filename 响应头、临时 zip 清理

运行方式：python -m pytest tests/test_batch_download.py -v
"""

import tempfile
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote
from zipfile import ZipFile

from fastapi.testclient import TestClient

import main
from music_downloader import DownloadException, DownloadResult

client = TestClient(main.app)


def test_tracks_required() -> None:
    r = client.post('/batch_download', json={'quality': 'standard'})
    assert r.status_code == 400
    body = r.json()
    assert body['success'] is False and 'tracks' in body['message'], body


def test_tracks_too_many() -> None:
    tracks = [{'id': str(i)} for i in range(main.BATCH_MAX_TRACKS + 1)]
    r = client.post('/batch_download', json={'tracks': tracks})
    assert r.status_code == 400 and '单批最多' in r.json()['message'], r.json()


def test_invalid_track_item() -> None:
    r = client.post('/batch_download', json={'tracks': [{'id': '1'}, 'not-a-dict']})
    assert r.status_code == 400 and '歌曲对象' in r.json()['message'], r.json()


def test_invalid_song_id() -> None:
    r = client.post('/batch_download', json={'tracks': [{'id': 'abc'}]})
    assert r.status_code == 400 and 'ID 不合法' in r.json()['message'], r.json()


def test_invalid_quality() -> None:
    r = client.post('/batch_download', json={'tracks': [{'id': '1'}], 'quality': 'flac'})
    assert r.status_code == 400 and '音质' in r.json()['message'], r.json()


class _FakeInfo:
    """最小 music_info 替身：路由仅访问 .id"""

    def __init__(self, song_id: int) -> None:
        self.id = song_id


def _fake_download_factory(contents: dict[int, bytes], fail_ids: set[int]):
    """返回 (假下载函数, 调用序列表)：fail_ids 中的单曲抛 DownloadException 模拟取链失败"""
    calls: list[int] = []

    def fake_download(music_id, quality='standard', cookies=None):
        song_id = int(music_id)
        calls.append(song_id)
        if song_id in fail_ids:
            raise DownloadException('模拟下载失败')
        path = tempfile.mkstemp(suffix='.flac')[1]
        with open(path, 'wb') as f:
            f.write(contents[song_id])
        return DownloadResult(
            success=True, file_path=path, file_size=len(contents[song_id]),
            music_info=_FakeInfo(song_id), cached=False,
        )

    return fake_download, calls


def test_batch_download_zips_local_files(tmp, monkeypatch) -> None:
    """打包链路：假下载器返回本地文件 → 校验 zip 内容、命名去非法字符、失败计数、临时文件清理"""
    contents = {101: b'aaa', 202: b'bbb'}
    fake_download, calls = _fake_download_factory(contents, fail_ids={303})

    # 隔离副作用：不写项目 storage 的访问量与排行榜
    monkeypatch.setattr(main.api_service.downloader, 'download_music_file', fake_download)
    monkeypatch.setattr(main.api_service.storage, 'record_access', lambda *_: None)
    monkeypatch.setattr(main.api_service.session_manager, 'record_download', lambda *_: None)

    tmp_dir = Path(tempfile.gettempdir())
    before = set(tmp_dir.glob('batch_*.zip'))

    r = client.post('/batch_download', json={
        'tracks': [
            {'id': '101', 'name': '歌一', 'artists': '歌手A'},
            {'id': '202', 'name': '歌二/含斜杠', 'artists': ''},
            {'id': '303', 'name': '失败歌', 'artists': '歌手C'},
        ],
        'quality': 'lossless',
        'name': '测试专辑',
    })

    assert r.status_code == 200, r.text[:300]
    assert r.headers['content-type'].startswith('application/zip')
    assert r.headers['X-Batch-Total'] == '3'
    assert r.headers['X-Batch-Success'] == '2'
    assert r.headers['X-Batch-Failed'] == '1'
    assert unquote(r.headers['X-Download-Filename']) == '测试专辑.zip'

    with ZipFile(BytesIO(r.content)) as zf:
        assert zf.namelist() == ['01. 歌一 - 歌手A.flac', '02. 歌二_含斜杠.flac']
        assert zf.read(zf.namelist()[0]) == b'aaa'
        assert zf.read(zf.namelist()[1]) == b'bbb'

    # 三首均进入下载器（含失败项），失败项仅计数不中断批次
    assert calls == [101, 202, 303], calls

    # 响应完成后临时 zip 已被 BackgroundTask 清理
    time.sleep(0.2)
    leftovers = set(tmp_dir.glob('batch_*.zip')) - before
    assert not leftovers, f'临时 zip 未清理: {leftovers}'
