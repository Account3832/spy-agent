# Backtest report
**UNDERLYING SIGNAL RESEARCH (SPY share prices). NOT option profitability.**

Data: SPY 2021-11-08 09:30:00-05:00 to 2026-10-23 15:55:00-04:00 (1244 trading days). Generated 2026-10-23 18:10:14.918854-04:00.


| Strategy | OOS trades | OOS net exp (R) | OOS CI | Verdict |
|---|---|---|---|---|
| trend_pullback | 391 | 0.102 | [-0.079, 0.274] | NOT_DEMONSTRATED |
| orb_retest | 289 | 0.005 | [-0.185, 0.23] | NOT_DEMONSTRATED |
| failed_breakout | 254 | -0.05 | [-0.257, 0.2] | NOT_DEMONSTRATED |
| range_reversion | 30 | -0.032 | [-0.503, 0.435] | INSUFFICIENT_SAMPLE |
| vol_expansion | 128 | 0.01 | [-0.288, 0.326] | NOT_DEMONSTRATED |
| orb_breakout | 187 | 0.058 | [-0.224, 0.356] | NOT_DEMONSTRATED |
| vwap_reclaim | 178 | 0.007 | [-0.227, 0.246] | NOT_DEMONSTRATED |
| gap_fade | 160 | 0.065 | [-0.193, 0.302] | NOT_DEMONSTRATED |
| prior_day_break | 79 | -0.127 | [-0.468, 0.206] | INSUFFICIENT_SAMPLE |
| steady_scalper | 450 | 0.01 | [-0.074, 0.093] | NOT_DEMONSTRATED |

Baseline (1h-trend, one trade/day) OOS: {'trades': 261, 'win_rate': 0.421, 'avg_win_R': 1.515, 'avg_loss_R': -0.997, 'gross_expectancy_R': 0.094, 'net_expectancy_R': 0.062, 'total_net_R': 16.08, 'profit_factor': 1.11, 'max_drawdown_R': 15.1, 'longest_losing_streak': 14, 't_stat': 0.76, 'ci_level': 0.995, 'ci_low': -0.159, 'ci_high': 0.287}

**Options research: UNAVAILABLE** - No free source of historical SPY option bid/ask quotes was found. Alpaca's free feed is indicative, not actual quotes, and cannot support execution-realistic option backtests.

Limitations:
- Bootstrap CIs ignore serial correlation between trades.
- Free intraday history is short; regime coverage may be poor.
- IEX-only volume understates real volume; VWAP/relative volume are approximations.
- Past results do not predict future results.