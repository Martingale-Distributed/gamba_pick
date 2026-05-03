@echo off
REM gamba-pick bootstrap (Windows). Installs uv if missing, syncs the
REM locked Python environment, fetches browsers on first run, and
REM exec's the gamba-pick CLI.
REM
REM NOTE: The uv installer SHA below was not verified at build time (the
REM dev machine for this plan was Linux). Customers who hit a hash mismatch
REM should follow README.txt's "Manual fallback" section.

setlocal EnableDelayedExpansion

set "UV_INSTALL_URL=https://astral.sh/uv/install.ps1"
set "UV_INSTALL_SHA256=REPLACE_BEFORE_RELEASE"

cd /d "%~dp0"

REM 1. Detect / install uv.
where uv >nul 2>&1
if errorlevel 1 (
    if not exist "%USERPROFILE%\.local\bin\uv.exe" (
        echo Installing uv...
        set "TMP_INSTALLER=%TEMP%\uv-install.ps1"
        powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing -Uri '%UV_INSTALL_URL%' -OutFile '%TMP_INSTALLER%'"
        if errorlevel 1 (
            echo ERROR: failed to download uv installer.
            exit /b 1
        )
        REM SHA256 verification.
        for /f "delims=" %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -Path '%TMP_INSTALLER%').Hash.ToLower()"') do set "ACTUAL_SHA=%%H"
        if /i not "!UV_INSTALL_SHA256!"=="REPLACE_BEFORE_RELEASE" (
            if /i not "!ACTUAL_SHA!"=="!UV_INSTALL_SHA256!" (
                echo ERROR: uv installer hash mismatch.
                echo   expected: !UV_INSTALL_SHA256!
                echo   actual:   !ACTUAL_SHA!
                exit /b 1
            )
        ) else (
            echo WARNING: uv installer SHA not pinned for Windows; skipping verification.
            echo See README.txt's manual fallback if this concerns you.
        )
        powershell -NoProfile -ExecutionPolicy Bypass -File "%TMP_INSTALLER%"
        del "%TMP_INSTALLER%"
    )
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

REM 2. Sync deps.
uv sync --frozen
if errorlevel 1 exit /b 1

REM 3. First-run browser fetch.
if not exist ".bootstrap-done" (
    echo First-run setup: fetching Camoufox + Patchright Chromium ^(~300 MB^)...
    uv run python -m camoufox fetch
    if errorlevel 1 exit /b 1
    uv run python -m patchright install chromium
    if errorlevel 1 exit /b 1
    type nul > .bootstrap-done
)

REM 4. Hand off.
uv run gamba-pick %*
