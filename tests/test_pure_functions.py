#!/usr/bin/env python3
"""
crawler_common / slide_libs の純粋関数に対する挙動固定テスト。

ネットワーク・tkinter に依存しないため、ヘッドレス環境（CI）で実行可能。
リファクタ前後で挙動が変わらないことを保証する回帰テストを兼ねる。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crawler_common as cc
import slide_libs as sl


# ========================================
# normalize_url
# ========================================
@pytest.mark.parametrize('url, expected', [
    # 拡張子なしパスは末尾スラッシュを補完
    ('https://example.com/about', 'https://example.com/about/'),
    # 既に末尾スラッシュならそのまま
    ('https://example.com/about/', 'https://example.com/about/'),
    # 拡張子ありはスラッシュを足さない
    ('https://example.com/page.html', 'https://example.com/page.html'),
    # query / fragment は除去
    ('https://example.com/p?a=1&b=2#sec', 'https://example.com/p/'),
    ('https://example.com/page.html?x=1', 'https://example.com/page.html'),
    # ルート
    ('https://example.com/', 'https://example.com/'),
])
def test_normalize_url(url, expected):
    assert cc.normalize_url(url) == expected


# ========================================
# is_skip_url
# ========================================
@pytest.mark.parametrize('url, expected', [
    ('https://example.com/file.pdf', True),
    ('https://example.com/img.JPG', True),       # 大文字拡張子
    ('https://example.com/style.css', True),
    ('https://example.com/script.js', True),
    ('https://example.com/page.html', False),    # html はスキップ対象外
    ('https://example.com/about/', False),        # 拡張子なし
    ('https://example.com/', False),
])
def test_is_skip_url(url, expected):
    assert cc.is_skip_url(url) is expected


# ========================================
# get_path_segments
# ========================================
@pytest.mark.parametrize('url, expected', [
    ('https://example.com/', []),
    ('https://example.com/a/', ['a']),
    ('https://example.com/a/b/c/', ['a', 'b', 'c']),
    ('https://example.com/a//b/', ['a', 'b']),   # 空セグメントは除去
])
def test_get_path_segments(url, expected):
    assert cc.get_path_segments(url) == expected


# ========================================
# extract_title / description / h1s
# ========================================
def test_extract_title():
    assert cc.extract_title('<html><title> Hello &amp; World </title></html>') == 'Hello & World'
    assert cc.extract_title('<html><head></head></html>') == ''
    # 大文字小文字・属性付きタグ
    assert cc.extract_title('<TITLE class="x">T</TITLE>') == 'T'


def test_extract_description():
    html_name_first = '<meta name="description" content="Desc &copy; here">'
    assert cc.extract_description(html_name_first) == 'Desc © here'
    # content が先、name が後の順序でも拾える
    html_content_first = '<meta content="Reversed" name="description">'
    assert cc.extract_description(html_content_first) == 'Reversed'
    assert cc.extract_description('<meta name="keywords" content="x">') == ''


def test_extract_h1s():
    html = '<h1>First</h1><h1 class="x"> Second <span>S</span></h1><h1></h1>'
    assert cc.extract_h1s(html) == ['First', 'Second S']
    # 空白の正規化
    assert cc.extract_h1s('<h1>  multi   space </h1>') == ['multi space']


# ========================================
# extract_links
# ========================================
def test_extract_links_same_domain_only():
    html = '''
        <a href="/about">about</a>
        <a href="https://example.com/news/">news</a>
        <a href="https://other.com/x">external</a>
        <a href="#anchor">anchor</a>
        <a href="mailto:a@b.com">mail</a>
        <a href="tel:123">tel</a>
        <a href="javascript:void(0)">js</a>
        <a href="/file.pdf">pdf</a>
    '''
    links = cc.extract_links(html, 'https://example.com/', 'example.com')
    assert links == {
        'https://example.com/about/',
        'https://example.com/news/',
    }


def test_extract_links_relative_resolution():
    html = '<a href="sub">x</a>'
    links = cc.extract_links(html, 'https://example.com/dir/page.html', 'example.com')
    assert links == {'https://example.com/dir/sub/'}


# ========================================
# detect_js_includes
# ========================================
def test_detect_js_includes():
    html = '''
        $("#header").load("/partials/header.html");
        fetch("/api/footer.php");
        fetch("/data.json");
        $(x).load("ignore.txt");
    '''
    assert cc.detect_js_includes(html) == {'/partials/header.html', '/api/footer.php'}


# ========================================
# fetch_with_retry の回数境界
# ========================================
class _FakeResp:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.headers = {}


def test_fetch_with_retry_single_attempt_success():
    """retry_count=1 で1回呼ばれ、成功レスポンスを返す。"""
    calls = []

    class S:
        def get(self, url, **kw):
            calls.append(url)
            return _FakeResp(200)

    resp, err = cc.fetch_with_retry(S(), 'https://x/', 10, 1, 0, lambda *_: None)
    assert err is None
    assert resp.status_code == 200
    assert len(calls) == 1


def test_fetch_with_retry_zero_guarded_to_one():
    """retry_count=0 でも最低1回試行する（旧バグの回帰防止）。"""
    calls = []

    class S:
        def get(self, url, **kw):
            calls.append(url)
            return _FakeResp(200)

    resp, err = cc.fetch_with_retry(S(), 'https://x/', 10, 0, 0, lambda *_: None)
    assert resp is not None
    assert len(calls) == 1


def test_fetch_with_retry_timeout_retries_then_gives_up():
    """タイムアウトが続くと retry_count 回試行して None を返す。"""
    import requests
    calls = []

    class S:
        def get(self, url, **kw):
            calls.append(url)
            raise requests.Timeout()

    resp, err = cc.fetch_with_retry(S(), 'https://x/', 10, 3, 0, lambda *_: None)
    assert resp is None
    assert err == 'TIMEOUT'
    assert len(calls) == 3


# ========================================
# detect_slide_libs（ネットワーク不要のケース）
# ========================================
def test_detect_slide_libs_in_use():
    """URLからバージョンが取れ、HTML構造ありで『使用中』判定。"""
    html = '''
        <link href="https://cdn.example/swiper@8.4.5/swiper.min.css">
        <script src="https://cdn.example/swiper@8.4.5/swiper.min.js"></script>
        <div class="swiper-container"><div class="swiper-wrapper"></div></div>
        <script>const s = new Swiper('.swiper-container', {});</script>
    '''
    # version は URL から取れるので session は使われない（None で十分）
    results = sl.detect_slide_libs(html, 'https://site.example/', session=None,
                                   timeout_sec=10, version_cache={})
    swiper = [r for r in results if r['name'] == 'Swiper']
    assert len(swiper) == 1
    assert swiper[0]['version'] == '8.4.5'
    assert swiper[0]['status'] == '使用中'


def test_detect_slide_libs_init_only():
    """初期化コードはあるがHTML構造クラスが無い → 『初期化のみ』。"""
    html = '''
        <script src="https://cdn.example/swiper@8.0.0/swiper.min.js"></script>
        <script>new Swiper('.foo', {});</script>
    '''
    results = sl.detect_slide_libs(html, 'https://site.example/', session=None,
                                   timeout_sec=10, version_cache={})
    assert results[0]['status'] == '初期化のみ（HTML構造なし）'


def test_detect_slide_libs_load_only():
    """読み込みのみで初期化もHTML構造も無い → 『読み込みのみ』。"""
    html = '<script src="https://cdn.example/swiper@8.0.0/swiper.min.js"></script>'
    results = sl.detect_slide_libs(html, 'https://site.example/', session=None,
                                   timeout_sec=10, version_cache={})
    assert results[0]['status'] == '読み込みのみ'


def test_detect_slide_libs_none():
    """スライドライブラリを含まないHTMLでは検出ゼロ。"""
    html = '<html><body><p>no slider here</p></body></html>'
    results = sl.detect_slide_libs(html, 'https://site.example/', session=None,
                                   timeout_sec=10, version_cache={})
    assert results == []
