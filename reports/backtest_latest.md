# Backtest report
**UNDERLYING SIGNAL RESEARCH (SPY share prices). NOT option profitability.**

Data: SPY 2021-11-08 09:30:00-05:00 to 2026-11-06 15:55:00-05:00 (1254 trading days). Generated 2026-11-06 17:21:47.835567-05:00.


| Strategy | OOS trades | OOS net exp (R) | OOS CI | Verdict |
|---|---|---|---|---|
| trend_pullback | 392 | 0.1 | [-0.074, 0.282] | NOT_DEMONSTRATED |
| orb_retest | 292 | -0.0 | [-0.217, 0.233] | NOT_DEMONSTRATED |
| failed_breakout | 259 | -0.05 | [-0.284, 0.175] | NOT_DEMONSTRATED |
| range_reversion | 32 | 0.032 | [-0.463, 0.516] | INSUFFICIENT_SAMPLE |
| vol_expansion | 130 | 0.044 | [-0.266, 0.4] | NOT_DEMONSTRATED |
| orb_breakout | 190 | 0.053 | [-0.216, 0.306] | NOT_DEMONSTRATED |
| vwap_reclaim | 178 | 0.01 | [-0.213, 0.293] | NOT_DEMONSTRATED |
| gap_fade | 161 | 0.052 | [-0.205, 0.3] | NOT_DEMONSTRATED |
| prior_day_break | 77 | -0.12 | [-0.463, 0.258] | INSUFFICIENT_SAMPLE |
| support_bounce | 118 | -0.031 | [-0.33, 0.315] | NOT_DEMONSTRATED |
| swing_breakout | 126 | -0.123 | [-0.388, 0.187] | NOT_DEMONSTRATED |
| steady_scalper | 451 | 0.01 | [-0.074, 0.094] | NOT_DEMONSTRATED |
| zone_rejection | 13 | -0.28 | [-1.022, 0.661] | INSUFFICIENT_SAMPLE |

## Chart-based exits vs fixed exits (same entries)
Verdict: **NOT_DEMONSTRATED** - the 95% range for the per-trade difference includes zero
Pooled out-of-sample: {'paired_trades': 181, 'mean_difference_R': -0.099, 'ci_low': -0.216, 'ci_high': 0.029, 'atr_exp_R': 0.123, 'chart_exp_R': 0.024}

Baseline (1h-trend, one trade/day) OOS: {'trades': 264, 'win_rate': 0.428, 'avg_win_R': 1.513, 'avg_loss_R': -0.997, 'gross_expectancy_R': 0.11, 'net_expectancy_R': 0.077, 'total_net_R': 20.44, 'profit_factor': 1.14, 'max_drawdown_R': 15.1, 'longest_losing_streak': 14, 't_stat': 0.95, 'ci_level': 0.9962, 'ci_low': -0.153, 'ci_high': 0.325}

**Options research: UNAVAILABLE** - No free source of historical SPY option bid/ask quotes was found. Alpaca's free feed is indicative, not actual quotes, and cannot support execution-realistic option backtests.

Limitations:
- Bootstrap CIs ignore serial correlation between trades.
- Free intraday history is short; regime coverage may be poor.
- IEX-only volume understates real volume; VWAP/relative volume are approximations.
- Past results do not predict future results.