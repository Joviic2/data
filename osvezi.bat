@echo off
rem Osvezi SVE podatke: fudbal (Premier liga, La Liga, Ligue 1, Bundesliga, Serie A) i kosarka. Poziva ga i pokreni.bat.
rem Svaki korak je odvojen: ako jedan pukne, ostali se svejedno izvrsavaju.
rem Redosled po ligi: Statshub -> sudije/stadioni -> Superbet kvote (+ pracenje pada kvota) -> kalibracija -> predikcije.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)

echo.
echo === Kosarka ===
if exist fetch_superbet.py %PY% fetch_superbet.py
if exist bb_ev.py %PY% bb_ev.py

if not exist fb_leagues.py (
  echo [PRESKACEM] Nema fb_leagues.py, fudbal nije podesen.
  goto kraj
)
call :liga epl "Premier liga"
call :liga laliga "La Liga"
call :liga l1 "Ligue 1"
call :liga bl "Bundesliga"
call :liga sa "Serie A"
goto kraj

:liga
echo.
echo === %~2 ===
%PY% statshub_football.py --league %~1
if errorlevel 1 echo [UPOZORENJE] Statshub za %~2 nije uspeo, nastavljam sa starim podacima ako ih ima.
if not exist data\%~1\statshub_data.json (
  echo [PRESKACEM] Nema data\%~1\statshub_data.json, %~2 se preskace.
  goto :eof
)
if exist fb_autodata.py %PY% fb_autodata.py --league %~1
if exist fb_table.py %PY% fb_table.py --league %~1
if errorlevel 1 echo [UPOZORENJE] Sudije/stadioni za %~2 nisu uspeli.
if exist fetch_superbet_football.py %PY% fetch_superbet_football.py --league %~1
if errorlevel 1 echo [UPOZORENJE] Superbet kvote za %~2 nisu uspele.
set LS=
if exist fb_calibrate.py (
  %PY% fb_calibrate.py --league %~1
  if exist data\%~1\calibration.json for /f %%i in ('%PY% -c "import json;print(json.load(open('data/%~1/calibration.json'))['lam_scale'])"') do set LS=%%i
)
if defined LS (
  %PY% football_poisson.py --league %~1 --beta 0.5 --lam-scale %LS%
) else (
  %PY% football_poisson.py --league %~1 --beta 0.5
)
if errorlevel 1 echo [UPOZORENJE] Predikcije za %~2 nisu uspele.
goto :eof

:kraj
echo.
echo Gotovo.
if /i not "%~1"=="nopause" pause
