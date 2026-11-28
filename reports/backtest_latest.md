# Backtest report
**UNDERLYING SIGNAL RESEARCH (SPY share prices). NOT option profitability.**

Data: SPY 2021-11-08 09:30:00-05:00 to 2026-11-27 13:15:00-05:00 (1268 trading days). Generated 2026-11-27 22:31:14.510114-05:00.


| Strategy | OOS trades | OOS net exp (R) | OOS CI | Verdict |
|---|---|---|---|---|
| trend_pullback | 402 | 0.111 | [-0.061, 0.308] | NOT_DEMONSTRATED |
| orb_retest | 291 | 0.029 | [-0.19, 0.252] | NOT_DEMONSTRATED |
| failed_breakout | 254 | -0.032 | [-0.254, 0.206] | NOT_DEMONSTRATED |
| range_reversion | 32 | 0.032 | [-0.463, 0.516] | INSUFFICIENT_SAMPLE |
| vol_expansion | 134 | 0.022 | [-0.299, 0.339] | NOT_DEMONSTRATED |
| orb_breakout | 191 | 0.098 | [-0.164, 0.369] | NOT_DEMONSTRATED |
| vwap_reclaim | 179 | 0.046 | [-0.223, 0.307] | NOT_DEMONSTRATED |
| gap_fade | 167 | 0.02 | [-0.217, 0.233] | NOT_DEMONSTRATED |
| prior_day_break | 78 | -0.131 | [-0.465, 0.255] | INSUFFICIENT_SAMPLE |
| support_bounce | 117 | -0.028 | [-0.34, 0.335] | NOT_DEMONSTRATED |
| swing_breakout | 127 | -0.102 | [-0.401, 0.237] | NOT_DEMONSTRATED |
| steady_scalper | 457 | 0.012 | [-0.078, 0.099] | NOT_DEMONSTRATED |
| zone_rejection | 13 | -0.28 | [-1.022, 0.661] | INSUFFICIENT_SAMPLE |

## Chart-based exits vs fixed exits (same entries)
Verdict: **NOT_DEMONSTRATED** - the 95% range for the per-trade difference includes zero
Pooled out-of-sample: {'paired_trades': 196, 'mean_difference_R': -0.113, 'ci_low': -0.222, 'ci_high': 0.001, 'atr_exp_R': 0.154, 'chart_exp_R': 0.041}

Baseline (1h-trend, one trade/day) OOS: {'trades': 265, 'win_rate': 0.43, 'avg_win_R': 1.525, 'avg_loss_R': -0.997, 'gross_expectancy_R': 0.119, 'net_expectancy_R': 0.088, 'total_net_R': 23.29, 'profit_factor': 1.15, 'max_drawdown_R': 15.1, 'longest_losing_streak': 14, 't_stat': 1.08, 'ci_level': 0.9962, 'ci_low': -0.137, 'ci_high': 0.318}

**Options research: UNAVAILABLE** - No free source of historical SPY option bid/ask quotes was found. Alpaca's free feed is indicative, not actual quotes, and cannot support execution-realistic option backtests.

Limitations:
- Bootstrap CIs ignore serial correlation between trades.
- Free intraday history is short; regime coverage may be poor.
- IEX-only volume understates real volume; VWAP/relative volume are approximations.
- Past results do not predict future results.