@echo off
REM Launch the pdf2fg CLI from this ruleset folder.
REM First time only, install deps:  pip install -r requirements.txt
cd /d "%~dp0"
python pdf2fg.py
pause
