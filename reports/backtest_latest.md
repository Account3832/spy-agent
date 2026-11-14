# Backtest report
**UNDERLYING SIGNAL RESEARCH (SPY share prices). NOT option profitability.**

Data: SPY 2021-11-08 09:30:00-05:00 to 2026-11-13 15:55:00-05:00 (1259 trading days). Generated 2026-11-13 20:47:26.411562-05:00.


| Strategy | OOS trades | OOS net exp (R) | OOS CI | Verdict |
|---|---|---|---|---|
| trend_pullback | 395 | 0.089 | [-0.071, 0.261] | NOT_DEMONSTRATED |
| orb_retest | 292 | 0.006 | [-0.209, 0.227] | NOT_DEMONSTRATED |
| failed_breakout | 259 | -0.044 | [-0.281, 0.189] | NOT_DEMONSTRATED |
| range_reversion | 32 | 0.032 | [-0.463, 0.516] | INSUFFICIENT_SAMPLE |
| vol_expansion | 133 | 0.032 | [-0.271, 0.381] | NOT_DEMONSTRATED |
| orb_breakout | 190 | 0.065 | [-0.181, 0.359] | NOT_DEMONSTRATED |
| vwap_reclaim | 177 | 0.003 | [-0.251, 0.268] | NOT_DEMONSTRATED |
| gap_fade | 162 | 0.046 | [-0.179, 0.302] | NOT_DEMONSTRATED |
| prior_day_break | 77 | -0.12 | [-0.473, 0.253] | INSUFFICIENT_SAMPLE |
| support_bounce | 118 | -0.044 | [-0.352, 0.28] | NOT_DEMONSTRATED |
| swing_breakout | 127 | -0.13 | [-0.406, 0.186] | NOT_DEMONSTRATED |
| steady_scalper | 453 | 0.008 | [-0.081, 0.091] | NOT_DEMONSTRATED |
| zone_rejection | 13 | -0.28 | [-1.022, 0.661] | INSUFFICIENT_SAMPLE |

## Chart-based exits vs fixed exits (same entries)
Verdict: **NOT_DEMONSTRATED** - the 95% range for the per-trade difference includes zero
Pooled out-of-sample: {'paired_trades': 187, 'mean_difference_R': -0.108, 'ci_low': -0.227, 'ci_high': 0.024, 'atr_exp_R': 0.113, 'chart_exp_R': 0.006}

Baseline (1h-trend, one trade/day) OOS: {'trades': 264, 'win_rate': 0.428, 'avg_win_R': 1.532, 'avg_loss_R': -0.997, 'gross_expectancy_R': 0.117, 'net_expectancy_R': 0.086, 'total_net_R': 22.6, 'profit_factor': 1.15, 'max_drawdown_R': 15.1, 'longest_losing_streak': 14, 't_stat': 1.04, 'ci_level': 0.9962, 'ci_low': -0.133, 'ci_high': 0.325}

**Options research: UNAVAILABLE** - No free source of historical SPY option bid/ask quotes was found. Alpaca's free feed is indicative, not actual quotes, and cannot support execution-realistic option backtests.

Limitations:
- Bootstrap CIs ignore serial correlation between trades.
- Free intraday history is short; regime coverage may be poor.
- IEX-only volume understates real volume; VWAP/relative volume are approximations.
- Past results do not predict future results.