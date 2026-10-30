# Backtest report
**UNDERLYING SIGNAL RESEARCH (SPY share prices). NOT option profitability.**

Data: SPY 2021-11-08 09:30:00-05:00 to 2026-10-30 15:55:00-04:00 (1249 trading days). Generated 2026-10-30 18:16:16.865707-04:00.


| Strategy | OOS trades | OOS net exp (R) | OOS CI | Verdict |
|---|---|---|---|---|
| trend_pullback | 392 | 0.1 | [-0.082, 0.274] | NOT_DEMONSTRATED |
| orb_retest | 291 | 0.009 | [-0.229, 0.233] | NOT_DEMONSTRATED |
| failed_breakout | 256 | -0.044 | [-0.262, 0.182] | NOT_DEMONSTRATED |
| range_reversion | 32 | 0.032 | [-0.463, 0.516] | INSUFFICIENT_SAMPLE |
| vol_expansion | 129 | 0.013 | [-0.282, 0.343] | NOT_DEMONSTRATED |
| orb_breakout | 189 | 0.059 | [-0.187, 0.337] | NOT_DEMONSTRATED |
| vwap_reclaim | 177 | 0.021 | [-0.257, 0.299] | NOT_DEMONSTRATED |
| gap_fade | 161 | 0.068 | [-0.193, 0.305] | NOT_DEMONSTRATED |
| prior_day_break | 79 | -0.127 | [-0.477, 0.224] | INSUFFICIENT_SAMPLE |
| support_bounce | 121 | -0.014 | [-0.308, 0.303] | NOT_DEMONSTRATED |
| swing_breakout | 125 | -0.116 | [-0.416, 0.207] | NOT_DEMONSTRATED |
| steady_scalper | 451 | 0.01 | [-0.075, 0.092] | NOT_DEMONSTRATED |
| zone_rejection | 12 | -0.219 | [-1.022, 0.792] | INSUFFICIENT_SAMPLE |

## Chart-based exits vs fixed exits (same entries)
Verdict: **NOT_DEMONSTRATED** - the 95% range for the per-trade difference includes zero
Pooled out-of-sample: {'paired_trades': 180, 'mean_difference_R': -0.1, 'ci_low': -0.221, 'ci_high': 0.024, 'atr_exp_R': 0.129, 'chart_exp_R': 0.03}

Baseline (1h-trend, one trade/day) OOS: {'trades': 264, 'win_rate': 0.42, 'avg_win_R': 1.515, 'avg_loss_R': -0.998, 'gross_expectancy_R': 0.091, 'net_expectancy_R': 0.059, 'total_net_R': 15.52, 'profit_factor': 1.1, 'max_drawdown_R': 15.1, 'longest_losing_streak': 14, 't_stat': 0.72, 'ci_level': 0.9962, 'ci_low': -0.169, 'ci_high': 0.306}

**Options research: UNAVAILABLE** - No free source of historical SPY option bid/ask quotes was found. Alpaca's free feed is indicative, not actual quotes, and cannot support execution-realistic option backtests.

Limitations:
- Bootstrap CIs ignore serial correlation between trades.
- Free intraday history is short; regime coverage may be poor.
- IEX-only volume understates real volume; VWAP/relative volume are approximations.
- Past results do not predict future results.