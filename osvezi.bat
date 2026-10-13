@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)

rem ============================================================================
rem  osvezi.bat  -  brze osvezavanje podataka (fudbal + kosarka)
rem
rem    osvezi.bat           normalno: Statshub se preskace za lige sa svezim podacima (< 6 h), kvote se uvek skidaju
rem    osvezi.bat brzo      samo kvote + predikcije (bez Statshub-a): za cesto osvezavanje, traje najkrace
rem    osvezi.bat puno      sve iznova, i Statshub i kalibracija (sporo, uradi ga jednom dnevno)
rem    dodaj  nopause       ne ceka pritisak tastera na kraju (koristi ga pokreni.bat)
rem
rem  Sta je ubrzano: (1) Statshub i Superbet rade ISTOVREMENO (dva razlicita sajta, svaki i dalje sa jednim tokom i
rem  istim pauzama kao pre); (2) predikcije po ligama rade paralelno (to je samo racunanje); (3) Statshub se preskace
rem  ako su podaci mladji od STATS_H sati; (4) ispis pokazuje vreme svakog koraka.
rem  Lige: edituj LIGE ispod, ili postavi OSVEZI_LIGE (npr. set OSVEZI_LIGE=epl laliga).
rem ============================================================================

rem ---------- pomocne grane: ovu istu skriptu pozivamo u pozadini ----------
if /i "%~1"=="--odds-lane" goto odds_lane
if /i "%~1"=="--poisson-lane" goto poisson_lane

set "LIGE=%OSVEZI_LIGE%"
if not defined LIGE set "LIGE=epl laliga l1 cl el conf"
if not defined STATS_H set "STATS_H=6"
set "MODE=normal"
set "DOPAUSE=1"
for %%A in (_ %*) do (
  if /i "%%~A"=="brzo" set "MODE=brzo"
  if /i "%%~A"=="puno" set "MODE=puno"
  if /i "%%~A"=="nopause" set "DOPAUSE=0"
)
if not exist logs mkdir logs
del /q logs\*.done 2>nul
set "T0=%time:~0,8%"
echo.
echo Start %T0%   nacin: %MODE%   lige: %LIGE%

if not exist fb_leagues.py (
  echo [UPOZORENJE] Nema fb_leagues.py, preskacem fudbal.
  set "LIGE=_"
)

rem ---------- 1) kvote (Superbet: kosarka + fudbal) u pozadini ----------
echo [%time:~0,8%] Kvote se skidaju u pozadini, log: logs\odds.log
start "" /b cmd /c ""%~f0" --odds-lane >logs\odds.log 2>&1"

rem ---------- 2) Statshub (statistika timova) u ovom prozoru ----------
if /i "%MODE%"=="brzo" (
  echo [%time:~0,8%] Brzi nacin: Statshub se preskace.
) else (
  for %%L in (%LIGE%) do call :stats %%L
)

rem ---------- 3) sacekaj kvote ----------
echo.
echo [%time:~0,8%] Cekam da se zavrsi skidanje kvota...
call :wait logs\odds.done 1200

rem ---------- 4) predikcije, sve lige paralelno ----------
echo.
echo [%time:~0,8%] Predikcije...
set "FL="
for %%L in (%LIGE%) do (
  if "%%L"=="_" (
    rem nema fudbalskih liga
  ) else if exist "data\%%L\statshub_data.json" (
    start "" /b cmd /c ""%~f0" --poisson-lane %%L >logs\poisson_%%L.log 2>&1"
    set "FL=!FL! logs\p_%%L.done"
  ) else (
    echo [PRESKACEM] Nema data\%%L\statshub_data.json, predikcije za %%L ne mogu da se naprave.
  )
)
if defined FL for %%F in (!FL!) do call :wait %%F 900

rem ---------- 5) ispis logova ----------
echo.
echo ===================== kvote =====================
if exist logs\odds.log type logs\odds.log
for %%L in (%LIGE%) do (
  if exist logs\poisson_%%L.log (
    echo.
    echo ===================== predikcije %%L =====================
    type logs\poisson_%%L.log
  )
)
echo.
echo Gotovo. Start %T0%, kraj %time:~0,8%.
if "%DOPAUSE%"=="1" pause
exit /b 0

rem ----------------------------------------------------------------------------
:stats
set "SL=%~1"
if "%SL%"=="_" goto :eof
if /i not "%MODE%"=="puno" (
  call :fresh "data\%SL%\statshub_data.json" %STATS_H%
  if not errorlevel 1 (
    echo [%time:~0,8%] %SL%: Statshub podaci su sveze ^(manje od %STATS_H% h^), preskacem. Prisilno: osvezi.bat puno
    goto :eof
  )
)
echo [%time:~0,8%] %SL%: Statshub...
%PY% statshub_football.py --league %SL%
if errorlevel 1 echo [UPOZORENJE] Statshub za %SL% nije uspeo, nastavljam sa starim podacima ako ih ima.
goto :eof

rem errorlevel 0 = fajl postoji i mladji je od N sati
:fresh
powershell -NoProfile -Command "if (Test-Path '%~1') { if (((Get-Date) - (Get-Item '%~1').LastWriteTime).TotalHours -lt %~2) { exit 0 } }; exit 1"
goto :eof

rem call :wait <fajl> <max sekundi>
:wait
set /a WT=0
:wait_loop
if exist "%~1" goto :eof
if !WT! geq %~2 (
  echo [UPOZORENJE] Isteklo je vreme cekanja na %~1
  goto :eof
)
timeout /t 2 /nobreak >nul
set /a WT+=2
goto wait_loop

rem ---------------------------- pozadinske grane ----------------------------
:odds_lane
if not defined LIGE set "LIGE=epl laliga l1 cl el conf"
echo [%time:~0,8%] === Kosarka ===
if defined ODDS_API_KEY if exist fetch_odds.py %PY% fetch_odds.py
if not exist data\odds.json echo [UPOZORENJE] Nema data\odds.json, pa EV nema Pinnacle referencu.
if exist fetch_superbet.py %PY% fetch_superbet.py
if exist fetch_superbet_football.py (
  for %%L in (%LIGE%) do if not "%%L"=="_" (
    echo.
    echo [%time:~0,8%] === Superbet fudbal: %%L ===
    %PY% fetch_superbet_football.py --league %%L
    if errorlevel 1 echo [UPOZORENJE] Superbet kvote za %%L nisu uspele.
  )
)
echo [%time:~0,8%] kvote gotove.
echo done>logs\odds.done
exit /b 0

:poisson_lane
set "PL=%~2"
set "LS="
set "ISCUP=0"
for %%C in (cl el conf) do if /i "%PL%"=="%%C" set "ISCUP=1"
if "%ISCUP%"=="0" if /i "%MODE%"=="puno" if exist fb_calibrate.py %PY% fb_calibrate.py --league %PL%
if exist data\%PL%\calibration.json for /f %%i in ('%PY% -c "import json;print(json.load(open('data/%PL%/calibration.json'))['lam_scale'])"') do set "LS=%%i"
if defined LS (
  %PY% football_poisson.py --league %PL% --beta 0.5 --lam-scale %LS%
) else (
  %PY% football_poisson.py --league %PL% --beta 0.5
)
if errorlevel 1 echo [UPOZORENJE] Predikcije za %PL% nisu uspele.
echo done>logs\p_%PL%.done
exit /b 0
