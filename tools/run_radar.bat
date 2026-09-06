@echo off
title Kijiji Winnipeg Arbitrage Radar
echo =================================================================
echo             KIJIJI WINNIPEG ARBITRAGE RADAR
echo =================================================================
echo Loading product database and starting background 5-min scanner...
echo Press Ctrl+C at any time to stop.
echo =================================================================
python main.py --interval 300
pause
