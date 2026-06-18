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


def test_extract_description_spaces_around_equals():
    # = の前後にスペースがあっても抽出できる
    html = '<meta name = "description" content = "Spaced">'
    assert cc.extract_description(html) == 'Spaced'


def test_extract_description_with_apostrophe():
    # content 値内にアポストロフィがあっても途中で切れない
    html = '<meta name="description" content="It\'s a great site">'
    assert cc.extract_description(html) == "It's a great site"
    # シングルクォートで囲み、値内にダブルクォート
    html2 = '<meta name=\'description\' content=\'Say "hi" now\'>'
    assert cc.extract_description(html2) == 'Say "hi" now'


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


def test_extract_links_spaces_around_equals():
    # href = "..." のように = の前後にスペースがあっても抽出できる
    html = '<a href = "/about">x</a><a href ="/news/">y</a>'
    links = cc.extract_links(html, 'https://example.com/', 'example.com')
    assert links == {'https://example.com/about/', 'https://example.com/news/'}


def test_extract_links_unquoted_attribute():
    # HTML5 のクォートなし href も抽出できる
    html = '<a href=/about>x</a> <a href=https://example.com/news/>y</a>'
    links = cc.extract_links(html, 'https://example.com/', 'example.com')
    assert links == {'https://example.com/about/', 'https://example.com/news/'}


def test_extract_description_unquoted_attribute():
    # クォートなし content も抽出できる
    html = '<meta name=description content=MyAwesomeSite>'
    assert cc.extract_description(html) == 'MyAwesomeSite'


def test_extract_links_apostrophe_in_path_not_truncated():
    # ダブルクォート href の値内にアポストロフィがあっても切れずに抽出する
    html = '<a href="/it\'s-page/">x</a>'
    links = cc.extract_links(html, 'https://example.com/', 'example.com')
    assert links == {"https://example.com/it's-page/"}


def test_extract_links_strips_whitespace_in_value():
    # 属性値の前後に空白がある場合も strip して正しく判定・結合する
    html = '<a href="  /about  ">x</a><a href=" mailto:a@b.com ">m</a><a href="   ">empty</a>'
    links = cc.extract_links(html, 'https://example.com/', 'example.com')
    assert links == {'https://example.com/about/'}


def test_open_path_swallows_oserror(monkeypatch):
    # xdg-open 等が無く Popen が OSError を投げても open_path は例外を出さない
    def _raise(*a, **k):
        raise FileNotFoundError('no opener')
    monkeypatch.setattr(cc.subprocess, 'Popen', _raise)
    monkeypatch.setattr(cc.os, 'startfile', _raise, raising=False)
    # 例外が送出されないことを確認（戻り値は None）
    assert cc.open_path('/tmp/whatever.csv') is None


# ========================================
# detect_js_includes
# ========================================
class _RecordingSession:
    """session.get の呼び出しURLを記録するだけのフェイク。"""
    def __init__(self):
        self.requested = []

    def get(self, url, **kw):
        self.requested.append(url)
        raise AssertionError('should not be called for external URL')


def test_fetch_js_includes_skips_external_domain():
    """JSインクルードが外部絶対URLを指す場合はリクエストしない（SSRF防止）。"""
    html = '$(x).load("https://evil.example/page.html");'
    sess = _RecordingSession()
    extra = cc.fetch_js_includes(
        sess, html, 'https://site.example/', 'site.example',
        timeout_sec=10, delay_sec=0, cache={}, log_fn=lambda *_: None)
    assert extra == set()
    assert sess.requested == []


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


def test_detect_slide_libs_spaces_around_equals():
    """script src= の = 前後にスペースがあっても読み込みを検出できる。"""
    html = '<script src = "https://cdn.example/swiper@8.4.5/swiper.min.js"></script>'
    results = sl.detect_slide_libs(html, 'https://site.example/', session=None,
                                   timeout_sec=10, version_cache={})
    swiper = [r for r in results if r['name'] == 'Swiper']
    assert len(swiper) == 1
    assert swiper[0]['version'] == '8.4.5'


def test_detect_slide_libs_init_not_excluded_by_src_word_in_other_attr():
    """他属性に src という単語を含むインラインscriptでも初期化を検出する。"""
    html = '''
        <script src="https://cdn.example/swiper@8.0.0/swiper.min.js"></script>
        <script id="src-loader">new Swiper('.foo', {});</script>
    '''
    results = sl.detect_slide_libs(html, 'https://site.example/', session=None,
                                   timeout_sec=10, version_cache={})
    assert results[0]['status'] == '初期化のみ（HTML構造なし）'


def test_detect_slide_libs_unquoted_src():
    """クォートなし src でもライブラリ読み込みを検出できる。"""
    html = '<script src=https://cdn.example/swiper@8.4.5/swiper.min.js></script>'
    results = sl.detect_slide_libs(html, 'https://site.example/', session=None,
                                   timeout_sec=10, version_cache={})
    swiper = [r for r in results if r['name'] == 'Swiper']
    assert len(swiper) == 1
    assert swiper[0]['version'] == '8.4.5'


def test_detect_slide_libs_strips_whitespace_in_src():
    """src 属性値の前後に空白があっても読み込み・バージョン抽出ができる。"""
    html = '<script src="  https://cdn.example/swiper@8.4.5/swiper.min.js  "></script>'
    results = sl.detect_slide_libs(html, 'https://site.example/', session=None,
                                   timeout_sec=10, version_cache={})
    swiper = [r for r in results if r['name'] == 'Swiper']
    assert len(swiper) == 1
    assert swiper[0]['version'] == '8.4.5'


def test_version_in_content_re_library_name_with_spaces():
    """コメントのライブラリ名にスペースが含まれてもバージョンを抽出できる。"""
    m = sl.VERSION_IN_CONTENT_RE.search('/*! Owl Carousel v2.3.4 */')
    assert m is not None
    assert next((g for g in m.groups() if g), '') == '2.3.4'
    # version:"x" 形式も従来通り
    m2 = sl.VERSION_IN_CONTENT_RE.search('e.version="8.4.5"')
    assert next((g for g in m2.groups() if g), '') == '8.4.5'
