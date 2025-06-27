@echo off

call C:\Users\Kyungsoo\anaconda3\Scripts\activate.bat py310

cd /d C:\Users\Kyungsoo\Documents\Code\Investment

pytest

start htmlcov\index.html
