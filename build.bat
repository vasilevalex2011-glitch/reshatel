@echo off
chcp 65001 >nul
echo === Сборка Reshatel.exe ===
where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Установите его: https://www.python.org/downloads/windows/
  echo При установке отметьте галочку "Add python.exe to PATH".
  pause
  exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Reshatel ^
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 ^
  solver.py
if errorlevel 1 (
  echo Сборка не удалась, см. ошибки выше.
  pause
  exit /b 1
)
echo.
echo Готово! Файл: dist\Reshatel.exe
pause
