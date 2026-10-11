@echo off
rem Pokrece server (bez kesa) i otvara sajt. Osvezavanje podataka je u osvezi.bat. Zatvori prozor da ugasis server.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
start "" cmd /c "timeout /t 2 /nobreak >nul & start "" http://localhost:8000/index.html"
%PY% -c "import http.server as h,socketserver as s;exec('class H(h.SimpleHTTPRequestHandler):\n def end_headers(self):\n  self.send_header(\"Cache-Control\",\"no-store\")\n  super().end_headers()');s.TCPServer.allow_reuse_address=True;print('Sajt radi na http://localhost:8000/index.html');s.TCPServer(('',8000),H).serve_forever()"
pause
