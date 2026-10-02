@echo off
rem ============================================================
rem  Bili_download_converter - PyInstaller build script
rem
rem  Usage:
rem    build.bat                     build a folder bundle (fast start, recommended)
rem    build.bat onefile             build a single .exe (easy to distribute)
rem    build.bat onedir D:\some\dir  custom output directory
rem
rem  NOTE: this file is intentionally ASCII-only. cmd.exe parses .bat
rem  files using the *system* codepage (GBK on Chinese Windows), so
rem  UTF-8 non-ASCII text here turns into garbage commands. Keep it ASCII.
rem
rem  Output goes to "%USERPROFILE%\Desktop\Bili_download_converter_v<ver>"
rem  by default, deliberately OUTSIDE the project so build artifacts
rem  never end up in the repository.
rem ============================================================

setlocal
cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=onedir"
set "APPNAME=Bili_download_converter"

echo.
echo ============================================================
echo  Bili_download_converter  build   mode: %MODE%
echo ============================================================
echo.

rem ---------- 0. environment ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found in PATH.
    pause
    exit /b 1
)

set "PYVER="
for /f "delims=" %%v in ('python -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%v"

set "VERSION="
for /f "delims=" %%v in ('python -c "import m4sconverter;print(m4sconverter.__version__)"') do set "VERSION=%%v"
if "%VERSION%"=="" set "VERSION=0.0.0"

echo [1/6] Python %PYVER%   version %VERSION%

set "OUTDIR=%~2"
if "%OUTDIR%"=="" set "OUTDIR=%USERPROFILE%\Desktop\%APPNAME%_v%VERSION%"
echo       output: %OUTDIR%

rem ---------- 1. dependencies ----------
echo [2/6] checking dependencies ...
python -c "import PyQt5" >nul 2>nul
if errorlevel 1 (
    echo       installing PyQt5 ...
    python -m pip install "PyQt5>=5.15" || goto :fail
)
python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo       installing PyInstaller ...
    python -m pip install --upgrade pyinstaller || goto :fail
)

rem ---------- 2. icon ----------
echo [3/6] checking icon ...
set "ICONOPT="
if exist "assets\icon.ico" (
    set "ICONOPT=--icon "%CD%\assets\icon.ico""
) else (
    echo       [WARN] assets\icon.ico not found, using the default icon.
)

rem ---------- 3. syntax check ----------
echo [4/6] compiling ...
python -m compileall -q m4sconverter gui cli.py main.py || goto :fail

rem ---------- 4. clean ----------
echo [5/6] cleaning old output ...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist "%APPNAME%.spec" del /q "%APPNAME%.spec"
if exist "%OUTDIR%" rmdir /s /q "%OUTDIR%"

rem ---------- 5. build ----------
echo [6/6] running PyInstaller ...
rem Paths MUST be absolute: --specpath moves the .spec into build\, and
rem relative --add-data / --icon are resolved against the spec's folder.
rem --add-data ships the icons inside the bundle: --icon only sets the
rem .exe icon, the window/taskbar icon is loaded at runtime via QIcon.
set "OPTS=--noconfirm --clean --windowed --name %APPNAME%"
set "OPTS=%OPTS% --add-data "%CD%\assets;assets""
set "OPTS=%OPTS% --distpath "%OUTDIR%" --workpath "build" --specpath "build""
if not "%ICONOPT%"=="" set "OPTS=%OPTS% %ICONOPT%"
if /i "%MODE%"=="onefile" set "OPTS=%OPTS% --onefile"

python -m PyInstaller %OPTS% main.py || goto :fail

rem ---------- 6. docs next to the exe ----------
if /i "%MODE%"=="onefile" (
    set "PKGDIR=%OUTDIR%"
) else (
    set "PKGDIR=%OUTDIR%\%APPNAME%"
)
if exist README.md copy /y README.md "%PKGDIR%\" >nul
if exist LICENSE   copy /y LICENSE   "%PKGDIR%\" >nul

echo.
echo ============================================================
echo   BUILD OK
if /i "%MODE%"=="onefile" (
    echo   output: %OUTDIR%\%APPNAME%.exe
) else (
    echo   output: %OUTDIR%\%APPNAME%\%APPNAME%.exe
)
echo.
echo   NOTE: the exe still needs an external ffmpeg.exe - either on
echo         PATH, or set its path in the "engine" section of the GUI.
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo [FAILED] aborted. See the error above.
pause
exit /b 1
