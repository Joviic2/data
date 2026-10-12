@echo off
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
if exist fb_live.py start "Live skener" cmd /k "%PY% fb_live.py --loop 45"
start "" cmd /c "timeout /t 2 /nobreak >nul & start "" http://localhost:8000/index.html"
%PY% -c "import http.server as h,socketserver as s;exec('class H(h.SimpleHTTPRequestHandler):\n def end_headers(self):\n  self.send_header(\"Cache-Control\",\"no-store\")\n  super().end_headers()');s.TCPServer.allow_reuse_address=True;print('Sajt radi na http://localhost:8000/index.html');s.TCPServer(('',8000),H).serve_forever()"
pause
