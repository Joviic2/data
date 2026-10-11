@echo off
rem Osvezi podatke (fudbal: Premier liga, La Liga, Ligue 1, plus kosarka). Pokreni pre pokreni.bat kad hoces sveze podatke.
rem Svaki korak je odvojen: ako jedan pukne, ostali se svejedno izvrsavaju. Nove lige idu poslednje da ne zadrzavaju ostalo.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)

echo.
echo === Premier liga ===
if exist statshub_football.py %PY% statshub_football.py
if exist football_poisson.py %PY% football_poisson.py --beta 0.5

echo.
echo === Kosarka ===
if exist fetch_superbet.py %PY% fetch_superbet.py

if not exist fb_leagues.py (
  echo [PRESKACEM] Nema fb_leagues.py, La Liga i Ligue 1 nisu podesene.
  goto kraj
)
call :liga laliga "La Liga"
call :liga l1 "Ligue 1"
goto kraj

:liga
echo.
echo === %~2 ===
%PY% statshub_football.py --league %~1
if errorlevel 1 echo [UPOZORENJE] Statshub za %~2 nije uspeo, nastavljam sa starim podacima ako ih ima.
if exist fetch_superbet_football.py %PY% fetch_superbet_football.py --league %~1
if errorlevel 1 echo [UPOZORENJE] Superbet kvote za %~2 nisu uspele.
if exist data\%~1\statshub_data.json (
  %PY% football_poisson.py --league %~1 --beta 0.5
  if errorlevel 1 echo [UPOZORENJE] Predikcije za %~2 nisu uspele.
) else (
  echo [PRESKACEM] Nema data\%~1\statshub_data.json, predikcije za %~2 ne mogu da se naprave.
)
goto :eof

:kraj
echo.
echo Gotovo.
pause
