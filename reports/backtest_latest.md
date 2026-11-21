# Backtest report
**UNDERLYING SIGNAL RESEARCH (SPY share prices). NOT option profitability.**

Data: SPY 2021-11-08 09:30:00-05:00 to 2026-11-20 15:55:00-05:00 (1264 trading days). Generated 2026-11-20 21:51:30.241540-05:00.


| Strategy | OOS trades | OOS net exp (R) | OOS CI | Verdict |
|---|---|---|---|---|
| trend_pullback | 400 | 0.114 | [-0.058, 0.283] | NOT_DEMONSTRATED |
| orb_retest | 292 | 0.023 | [-0.191, 0.243] | NOT_DEMONSTRATED |
| failed_breakout | 255 | -0.028 | [-0.248, 0.222] | NOT_DEMONSTRATED |
| range_reversion | 32 | 0.032 | [-0.463, 0.516] | INSUFFICIENT_SAMPLE |
| vol_expansion | 135 | 0.028 | [-0.305, 0.352] | NOT_DEMONSTRATED |
| orb_breakout | 191 | 0.102 | [-0.141, 0.372] | NOT_DEMONSTRATED |
| vwap_reclaim | 179 | 0.044 | [-0.235, 0.311] | NOT_DEMONSTRATED |
| gap_fade | 166 | 0.045 | [-0.187, 0.277] | NOT_DEMONSTRATED |
| prior_day_break | 78 | -0.131 | [-0.465, 0.255] | INSUFFICIENT_SAMPLE |
| support_bounce | 119 | -0.024 | [-0.348, 0.296] | NOT_DEMONSTRATED |
| swing_breakout | 128 | -0.098 | [-0.391, 0.217] | NOT_DEMONSTRATED |
| steady_scalper | 457 | 0.016 | [-0.069, 0.109] | NOT_DEMONSTRATED |
| zone_rejection | 13 | -0.28 | [-1.022, 0.661] | INSUFFICIENT_SAMPLE |

## Chart-based exits vs fixed exits (same entries)
Verdict: **NOT_DEMONSTRATED** - the 95% range for the per-trade difference includes zero
Pooled out-of-sample: {'paired_trades': 195, 'mean_difference_R': -0.108, 'ci_low': -0.224, 'ci_high': 0.009, 'atr_exp_R': 0.151, 'chart_exp_R': 0.042}

Baseline (1h-trend, one trade/day) OOS: {'trades': 266, 'win_rate': 0.436, 'avg_win_R': 1.525, 'avg_loss_R': -0.997, 'gross_expectancy_R': 0.134, 'net_expectancy_R': 0.103, 'total_net_R': 27.38, 'profit_factor': 1.18, 'max_drawdown_R': 15.1, 'longest_losing_streak': 14, 't_stat': 1.26, 'ci_level': 0.9962, 'ci_low': -0.123, 'ci_high': 0.346}

**Options research: UNAVAILABLE** - No free source of historical SPY option bid/ask quotes was found. Alpaca's free feed is indicative, not actual quotes, and cannot support execution-realistic option backtests.

Limitations:
- Bootstrap CIs ignore serial correlation between trades.
- Free intraday history is short; regime coverage may be poor.
- IEX-only volume understates real volume; VWAP/relative volume are approximations.
- Past results do not predict future results.