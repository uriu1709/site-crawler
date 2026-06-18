# サイトクローラー

指定したURLを起点に同一ドメイン内のリンクを自動的に辿り、各ページの **title / description / H1** を収集して CSV に出力するツール。
Webサイトのディレクトリマップ（サイトマップ一覧）を作成する用途で使用する。

## 概要

- 開始URLから同一ドメインの `<a href>` リンクをBFS（幅優先探索）で巡回
- 各HTMLページから title, meta description, h1 を正規表現で抽出
- パスの階層構造をセグメント列（seg0, seg1, ...）としてCSVに展開
- GUI（tkinter）で操作し、PyInstallerで単体exeにビルド可能

## ファイル構成

```
サイトクローラー/
├── site_crawler_gui.py         # サイトクローラー本体（exe化対象）
├── slide_lib_checker_gui.py    # スライドライブラリチェッカー本体（exe化対象）
├── crawler_common.py           # 両ツール共通のネットワーク・HTML解析ロジック（tkinter非依存）
├── slide_libs.py               # スライドライブラリ検出ロジック（tkinter非依存）
├── tests/
│   └── test_pure_functions.py  # 純粋関数の挙動固定テスト（pytest）
├── .github/workflows/ci.yml    # CI（pytest 自動実行）
├── requirements.txt            # 実行時依存
├── requirements-dev.txt        # 開発用依存（pytest, pyinstaller）
├── サイトクローラー.spec          # PyInstaller ビルド設定
├── スライドライブラリチェッカー.spec  # PyInstaller ビルド設定
├── .gitignore
└── README.md
```

> `crawler_common.py` / `slide_libs.py` は GUI（tkinter）に依存しないため、
> ヘッドレス環境でも import・テストが可能。GUI ファイルから import して使う。

### 実行時に生成されるファイル

| ファイル | 説明 |
|---------|------|
| `crawler_settings.json` | サイトクローラーのGUI設定の自動保存（URL、CSV保存先など） |
| `slidecheck_settings.json` | スライドライブラリチェッカーのGUI設定の自動保存 |
| `logs/crawl_YYYYMMDD_HHMMSS.log` | クロールログ（日時付き） |
| `logs/slidecheck_YYYYMMDD_HHMMSS.log` | チェッカーログ（日時付き） |
| 指定したCSVファイル | 実行結果 |

## 機能一覧

### 基本機能
- **BFSクロール**: 開始URLから同一ドメインのリンクを幅優先で巡回
- **HTML解析**: title, meta description, h1 を正規表現で抽出
- **CSV出力**: パス階層をセグメント列に展開し、UTF-8 BOM付きで出力
- **robots.txt対応**: robots.txt を読み込み、Disallow対象をスキップ（ON/OFF可能）

### SSL互換性
- `_SSLAdapter` により、DH鍵サイズ不足等の古いサーバーにも接続可能
- SSLセキュリティレベルを `SECLEVEL=1` に緩和して対応

### robots.txt取得の改善
- Python標準の `urllib.robotparser.read()` ではなく、`requests` セッション経由で取得
- SSL対応アダプタが適用されるため、古いサーバーでもrobots.txt取得に失敗しない
- 取得失敗時は「robots.txtなし」として続行（全ページブロックにならない）

### エンコーディング対応
- HTTPヘッダーにcharset指定がない場合（`ISO-8859-1` フォールバック）を検出
- requests 同梱の文字コード推定（`resp.apparent_encoding`）にフォールバック
- 日本語サイトの文字化けを防止

### フィルタ機能
- **除外ディレクトリ**: 指定パス配下を完全スキップ（例: `/wp-content/uploads/`）
- **まとめるディレクトリ**: 指定パス配下のページを代表1件のみ記録（例: `/news/`）
- **WordPress投稿自動まとめ**: `/YYYY/MM/` 形式の日付パーマリンクを自動検出してまとめ
- **ページネーションスキップ**: `/page/N/` パターンを自動除外

### JSインクルード対応
- jQuery `.load()` や `fetch()` でヘッダー・フッター等を動的に読み込むサイトに対応
- HTMLからインクルードパターンを自動検出し、`X-Requested-With: XMLHttpRequest` ヘッダー付きで取得
- インクルードファイル内のリンクもクロール対象に追加
- 同一ファイルはキャッシュして重複取得しない

### リトライ・エラー処理
- タイムアウト・接続エラー時に指定回数リトライ
- リトライ間隔を設定可能
- エラー種別（TIMEOUT / ERROR / HTTP_xxx）をCSVに記録

### ログ出力
- GUIのログエリアにリアルタイム表示
- `logs/` ディレクトリにタイムスタンプ付きログファイルを自動保存
- ログファイルはexe（またはスクリプト）と同じディレクトリに作成

### 設定の永続化
- GUI入力値を `crawler_settings.json` に自動保存
- 次回起動時に前回の設定を自動復元

### その他
- リダイレクト先URLの重複記録を防止
- 非HTMLコンテンツ（PDF、画像等）は拡張子ベースでスキップ
- クロール中断ボタンあり
- 完了後「CSVを開く」ボタンで直接ファイルを開ける

## GUI設定項目

| 項目 | デフォルト値 | 説明 |
|------|------------|------|
| クロール開始URL | （空欄） | クロールの起点URL |
| 出力CSVファイル | `<exeと同じ場所>/site_crawl_result.csv` | CSV出力先パス |
| 最大ページ数 | 2000 | クロール上限 |
| リクエスト間隔 | 0.5秒 | ページ間の待機時間 |
| タイムアウト | 20秒 | 1リクエストのタイムアウト |
| 試行回数（リトライ含む） | 3回 | 1リクエストの最大試行回数。最低1（1=リトライなし） |
| リトライ待機 | 3.0秒 | リトライ前の待機時間 |
| robots.txtを尊重 | ON | robots.txtに従うか |
| WordPress投稿自動まとめ | OFF | 日付パーマリンク検出 |
| ページネーションスキップ | OFF | /page/N/ 除外 |

## CSV出力形式

```
seg0, seg1, seg2, ..., segN, url, status, title, description, h1
```

- **seg0**: ルート（トップページ）のタイトルが入る
- **seg1〜segN**: 各階層。そのページが属する最下層のセグメント列にのみタイトルを配置
- **上位階層のセルは空**: ツリー構造を表現するため、タイトルは最深セグメントにのみ出力
- **列数は自動可変**: クロール結果の最深パスに合わせてseg列数が決まる
- **エンコーディング**: UTF-8 BOM付き（Excelで直接開いても文字化けしない）

### 出力例

| seg0 | seg1 | seg2 | url | status | title |
|------|------|------|-----|--------|-------|
| トップページ | | | https://example.com/ | 200 | トップページ |
| | 会社概要 | | https://example.com/about/ | 200 | 会社概要 |
| | | 代表挨拶 | https://example.com/about/greeting/ | 200 | 代表挨拶 |
| | お知らせ | | https://example.com/news/ | 200 | お知らせ |

## スキップ対象の拡張子

以下の拡張子を持つURLはクロール対象外（キューに追加しない）:

`.pdf` `.jpg` `.jpeg` `.png` `.gif` `.webp` `.svg` `.zip` `.docx` `.xlsx` `.pptx` `.doc` `.xls` `.ppt` `.mp4` `.mp3` `.mov` `.avi` `.wmv` `.css` `.js` `.ico` `.woff` `.woff2` `.ttf` `.eot`

## 動作環境

- **OS**: Windows 10/11
- **Python**: 3.10以上
- **依存パッケージ**: `requests`（標準ライブラリ以外で必要なのはこれのみ）

## セットアップ

```bash
pip install -r requirements.txt
```

## 実行方法

### スクリプトとして実行

```bash
python site_crawler_gui.py        # サイトクローラー
python slide_lib_checker_gui.py   # スライドライブラリチェッカー
```

## テスト

ネットワーク・GUI に依存しない純粋ロジック（`crawler_common.py` / `slide_libs.py`）には
pytest テストを用意している。push / PR 時に GitHub Actions（`.github/workflows/ci.yml`）で
Python 3.10〜3.12 に対して自動実行される。

```bash
pip install -r requirements-dev.txt
pytest -v
```

### exeビルド

#### かんたんビルド（推奨）

`build.bat` を**ダブルクリック**するだけで、2つのexeが生成される。
内部で次を自動実行する: Python検出 → 仮想環境（`.venv`）作成 → 依存パッケージ
インストール → 両 `.spec` を PyInstaller でビルド。完了後 `dist` フォルダが開く。

> 事前に Python 3.10以上のインストールが必要（インストール時に「Add Python to PATH」を
> 有効にしておくこと）。それ以外の準備は不要。

#### 手動ビルド

```bash
pip install pyinstaller
pyinstaller サイトクローラー.spec
pyinstaller スライドライブラリチェッカー.spec
```

ビルド後、`dist/サイトクローラー.exe` と `dist/スライドライブラリチェッカー.exe` が生成される。

### exe実行時の注意

- `crawler_settings.json` と `logs/` ディレクトリはexeと同じ場所に作成される
- exeを更新（再ビルド）する際は、実行中のexeを先に閉じること（PermissionError防止）

## リポジトリ

GitHub: [uriu1709/site-crawler](https://github.com/uriu1709/site-crawler)
