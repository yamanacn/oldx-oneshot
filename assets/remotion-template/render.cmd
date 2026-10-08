@echo off
rem Render the full video: GPU browser rendering (ANGLE), parallel tabs, NVENC encoding.
rem Media or words changed? Run scripts/packaging/remotion_new.py on the project first.
cd /d "%~dp0"
set /a CONC=NUMBER_OF_PROCESSORS/2
if %CONC% LSS 1 set CONC=1
if %CONC% GTR 16 set CONC=16
set NVENC=1
call npx remotion render src/index.ts Main out/_raw.mp4 --concurrency=%CONC% --hardware-acceleration=required --video-bitrate=12M --audio-bitrate=192k
if errorlevel 1 (
  echo NVENC failed, falling back to CPU encoding...
  set NVENC=
  call npx remotion render src/index.ts Main out/_raw.mp4 --concurrency=%CONC%
)
if not exist out\_raw.mp4 exit /b 1
rem Strip container metadata without re-encoding; picture and sound are untouched.
call npx remotion ffmpeg -y -v error -i out/_raw.mp4 -map_metadata -1 -map_chapters -1 -c copy -movflags +faststart out/main.mp4
if errorlevel 1 exit /b 1
del out\_raw.mp4
echo Done: out\main.mp4
