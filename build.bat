@echo off
chcp 65001 >nul
setlocal enableextensions
cd /d "%~dp0"

echo ==================================================
echo  サイトクローラー / スライドライブラリチェッカー
echo  ビルドスクリプト（exe を生成します）
echo ==================================================
echo.

REM --- Python を探す（py ランチャー優先） ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY goto :nopython

echo [1/3] 仮想環境 .venv を準備しています...
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto :venvfail
)
call ".venv\Scripts\activate.bat"

echo [2/3] 必要なパッケージをインストールしています...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :pipfail

echo [3/3] exe をビルドしています（数分かかる場合があります）...
for %%f in (*.spec) do (
    echo     - %%f
    pyinstaller --noconfirm --clean "%%f"
    if errorlevel 1 goto :buildfail
)

echo.
echo ==================================================
echo  ✅ 完了しました！
echo  生成された exe は dist フォルダにあります:
echo      dist\サイトクローラー.exe
echo      dist\スライドライブラリチェッカー.exe
echo ==================================================
echo.
if exist "dist" explorer "dist"
echo 何かキーを押すと終了します。
pause >nul
goto :eof

:nopython
echo [エラー] Python が見つかりませんでした。
echo         https://www.python.org/downloads/ からインストールし、
echo         インストール時に "Add Python to PATH" にチェックを入れてください。
goto :fail
:venvfail
echo [エラー] 仮想環境（.venv）の作成に失敗しました。
goto :fail
:pipfail
echo [エラー] 必要なパッケージのインストールに失敗しました。
echo         インターネット接続を確認してください。
goto :fail
:buildfail
echo [エラー] exe のビルドに失敗しました。上のログを確認してください。
goto :fail
:fail
echo.
echo 何かキーを押すと終了します。
pause >nul
exit /b 1
