# Backtest report
**UNDERLYING SIGNAL RESEARCH (SPY share prices). NOT option profitability.**

Data: SPY 2021-11-08 09:30:00-05:00 to 2026-10-16 15:55:00-04:00 (1239 trading days). Generated 2026-10-17 14:16:01.532136-04:00.


| Strategy | OOS trades | OOS net exp (R) | OOS CI | Verdict |
|---|---|---|---|---|
| trend_pullback | 384 | 0.113 | [-0.06, 0.298] | NOT_DEMONSTRATED |
| orb_retest | 290 | 0.013 | [-0.197, 0.235] | NOT_DEMONSTRATED |
| failed_breakout | 255 | -0.054 | [-0.253, 0.139] | NOT_DEMONSTRATED |
| range_reversion | 31 | -0.02 | [-0.497, 0.437] | INSUFFICIENT_SAMPLE |
| vol_expansion | 131 | 0.0 | [-0.304, 0.338] | NOT_DEMONSTRATED |
| orb_breakout | 188 | 0.051 | [-0.193, 0.289] | NOT_DEMONSTRATED |
| vwap_reclaim | 175 | 0.017 | [-0.251, 0.271] | NOT_DEMONSTRATED |
| gap_fade | 156 | 0.067 | [-0.186, 0.332] | NOT_DEMONSTRATED |
| prior_day_break | 80 | -0.131 | [-0.468, 0.238] | INSUFFICIENT_SAMPLE |
| steady_scalper | 443 | 0.017 | [-0.066, 0.099] | NOT_DEMONSTRATED |

Baseline (1h-trend, one trade/day) OOS: {'trades': 259, 'win_rate': 0.425, 'avg_win_R': 1.514, 'avg_loss_R': -0.998, 'gross_expectancy_R': 0.103, 'net_expectancy_R': 0.069, 'total_net_R': 17.95, 'profit_factor': 1.12, 'max_drawdown_R': 15.1, 'longest_losing_streak': 14, 't_stat': 0.85, 'ci_level': 0.995, 'ci_low': -0.146, 'ci_high': 0.319}

**Options research: UNAVAILABLE** - No free source of historical SPY option bid/ask quotes was found. Alpaca's free feed is indicative, not actual quotes, and cannot support execution-realistic option backtests.

Limitations:
- Bootstrap CIs ignore serial correlation between trades.
- Free intraday history is short; regime coverage may be poor.
- IEX-only volume understates real volume; VWAP/relative volume are approximations.
- Past results do not predict future results.