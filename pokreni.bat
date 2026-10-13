@echo off
<<<<<<< HEAD
rem Odmah pokrene sajt (sa starim podacima) + live skener, a osvezavanje podataka ide ISTOVREMENO u posebnom prozoru i na kraju salje na GitHub.
rem   pokreni.bat          = sajt odmah; u pozadini osvezi.bat (Statshub se preskace ako je mladji od 6 h) + push
rem   pokreni.bat puno     = isto, ali osvezi.bat puno (sve iznova + kalibracija, sporo)
rem   pokreni.bat brzo     = samo sajt i live skener, bez osvezavanja
rem Kad se prozor "Osvezavanje" zavrsi, osvezi stranicu (F5). Zatvori ovaj prozor da ugasis server.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
set "MODE=%~1"
if /i not "%MODE%"=="brzo" (
  if /i "%MODE%"=="puno" (set "OSV=osvezi.bat puno nopause") else (set "OSV=osvezi.bat nopause")
  start "Osvezavanje" cmd /c "call %OSV% & echo. & echo Saljem podatke na GitHub... & git add data >nul 2>nul & git commit -m "auto osvezavanje podataka" >nul 2>nul & git pull --rebase --autostash -X theirs origin main >nul 2>nul & (git push origin HEAD:main && echo Podaci poslati na GitHub. || echo [UPOZORENJE] Push nije uspeo, proveri: git status) & echo. & echo Gotovo - osvezi stranicu (F5). & pause"
)
rem ugasi stari server na portu 8000 (ako je ostao otvoren iz drugog foldera, pregledac bi i dalje prikazivao STARI index.html)
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>nul
echo Sajt se servira iz: %CD%
=======
rem Osvezi sve podatke pa pokrene server i otvori sajt. Zatvori prozor da ugasis server.
rem   pokreni.bat          = prvo osvezi sve, pa sajt
rem   pokreni.bat brzo     = odmah sajt, bez osvezavanja
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
if /i not "%~1"=="brzo" (
  echo Osvezavam podatke, to traje nekoliko minuta...
  call osvezi.bat nopause
)
rem Live skener (fb_live.py) u posebnom prozoru: osvezava data\live.json na ~45 s, sajt ga sam cita (tab UZIVO)
>>>>>>> c0636432980d531347fbd7fa74496488ec3a9d81
if exist fb_live.py start "Live skener" cmd /k "%PY% fb_live.py --loop 45"
start "" cmd /c "timeout /t 2 /nobreak >nul & start "" http://localhost:8000/index.html"
%PY% -c "import http.server as h,socketserver as s;exec('class H(h.SimpleHTTPRequestHandler):\n def end_headers(self):\n  self.send_header(\"Cache-Control\",\"no-store\")\n  super().end_headers()');s.TCPServer.allow_reuse_address=True;print('Sajt radi na http://localhost:8000/index.html');s.TCPServer(('',8000),H).serve_forever()"
pause
