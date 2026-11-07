@echo off
rem Сборка Mixer.exe (один файл, без консоли). Запускать на Windows из этой папки.
python -m pip install -r requirements.txt || goto :err
python -m PyInstaller --noconfirm --onefile --windowed --name Mixer ^
  --hidden-import win32timezone --collect-submodules webview ^
  mixer_agent.py || goto :err
echo.
echo Готово: dist\Mixer.exe
exit /b 0
:err
echo Сборка не удалась
exit /b 1
