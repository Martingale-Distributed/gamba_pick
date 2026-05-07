@echo off
REM gamba-pick bootstrap (Windows). Installs uv if missing, syncs the
REM locked Python environment, fetches browsers on first run, and
REM exec's the gamba-pick CLI.
REM
REM SECURITY: ``UV_INSTALL_SHA256`` below MUST be replaced with the
REM actual SHA256 of ``https://astral.sh/uv/install.ps1`` before this
REM script is shipped. The release process (operator machine with
REM Windows access) computes the hash via:
REM
REM    powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -Path 'install.ps1').Hash.ToLower()"
REM
REM and pastes the lowercase hex into ``UV_INSTALL_SHA256``. Until then
REM this script fails closed — running with the placeholder value would
REM execute downloaded code without integrity verification, which is a
REM remote-code-execution path and unacceptable for a customer release.

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
        REM SHA256 verification — fail closed on the placeholder so we
        REM never execute downloaded code without integrity checking.
        if /i "!UV_INSTALL_SHA256!"=="REPLACE_BEFORE_RELEASE" (
            echo ERROR: UV_INSTALL_SHA256 was not pinned before release.
            echo Refusing to execute the downloaded uv installer without integrity verification.
            echo The release operator must compute the SHA256 of install.ps1 and edit run.cmd.
            del "%TMP_INSTALLER%" 2>nul
            exit /b 1
        )
        for /f "delims=" %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -Path '%TMP_INSTALLER%').Hash.ToLower()"') do set "ACTUAL_SHA=%%H"
        if /i not "!ACTUAL_SHA!"=="!UV_INSTALL_SHA256!" (
            echo ERROR: uv installer hash mismatch.
            echo   expected: !UV_INSTALL_SHA256!
            echo   actual:   !ACTUAL_SHA!
            del "%TMP_INSTALLER%" 2>nul
            exit /b 1
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
