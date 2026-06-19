# Findings: What is causing Titan's revenue drop, and what should they do about it?

Total transaction value fell even though transaction counts held steady
because the **average amount per transfer dropped ~27-28% across every
corridor** in the most recent 30 days versus the prior 30 — this is a
platform-wide shift in sender behavior, not one bad corridor, and it lines up
with the Q4 2024 Yuno migration. The clearest differentiator between
corridors isn't the ATV decline itself (that's uniform) but **drop-off and
failure rates**: USA→Philippines, UK→Nigeria, and Canada→India see 23-29%
drop-off versus 8-11% for the healthiest corridors, and within those three
lanes, **cash pickup and mobile wallet fail 2-3x more often** than bank
transfer. Mobile wallet also has the lowest ATV of any payout method
($181.75 vs $265.45 for bank transfer), consistent with senders sending
smaller, lower-risk amounts through the routes they trust least. Weighting
ATV decline by transaction volume *and* failure rate (see "Top Problem
Areas" in `analytics.py` / the dashboard) ranks **USA→Philippines cash
pickup, UK→Nigeria cash pickup, and USA→Philippines mobile wallet** as the
highest-revenue-risk combinations — together with an estimated $140K+/month
in recoverable transaction volume if their ATV recovered to its prior-30-day
level. The pattern is consistent with senders losing trust in
high-friction routes and reacting rationally — sending smaller "test"
amounts or splitting transfers — rather than abandoning the platform
outright (counts stayed flat).

**Recommendation:** prioritize re-tuning Yuno's orchestration logic for cash
pickup and mobile wallet payouts in the USA→Philippines and UK→Nigeria
corridors first, since that's where the combination of high failure rate,
high volume, and ATV decline produces the largest estimated revenue
recovery — and fixing failure rates there should restore sender confidence
and let transfer sizes recover, without needing to touch the
otherwise-healthy corridors.
