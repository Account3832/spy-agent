# Data source capability and cost report

Generated from spxai/providers.py. Evidence levels are as recorded in the registry; re-verify before relying on them.

| Provider | Cost/mo | Real-time class | SPY prices | SPY options | Bid/ask | Automated use | Enabled | Evidence |
|---|---|---|---|---|---|---|---|---|
| alpaca_equities_iex | $0 | realtime_licensed | yes (SPY, IEX exchange only) | no | IEX-only quotes | allowed | yes | official docs |
| yahoo_finance_unofficial | $0 | unofficial_unverified | yes (SPY) | no | none | unverified | yes | not verified this session |
| alpaca_options_indicative | $0 | indicative | no | likely yes for SPY (unverified), indicative only | indicative (not actual OPRA quotes) | allowed | no | official docs |
| opra_via_alpaca | $99 | realtime_licensed | no | yes | yes | allowed | no | official docs |
| intrinio_options_15min | $800 | delayed | no | yes | yes (delayed) | allowed | no | official docs |
| interactive_brokers_api | $0 | realtime_licensed | yes | yes | yes | allowed | no | not verified this session |
| cboe_delayed_web_pages | $0 | delayed | n/a (Cboe focus) | n/a (Cboe focus) | delayed | prohibited | no | third-party summary |