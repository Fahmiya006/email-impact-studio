# Memo: Which email should we send, and to whom?

**To:** Head of Marketing
**From:** Analytics
**Re:** Results of the three-arm email test (64,000 customers)

## Recommendation

1. **Send the men's email to the entire list.** It lifts average spend from $0.65 to $1.42 per customer in the two weeks after the send. At a 40% margin and $0.10 per email, that is about **$0.21 more profit per customer than sending nothing, roughly $208,000 per million customers**.
2. **Make the women's email a non-default.** It lifts spend by $0.42 per customer, against $0.77 for the men's email. The $0.35 gap (95% CI $0.03 to $0.66) is worth about $0.14 of profit per customer, or $138,000 per million.
3. **Do not deploy individual-level targeting yet.** The uplift model beats "men's email to everyone" by $0.013 per customer, with a 95% interval of -$0.010 to +$0.039. That is indistinguishable from zero, and not worth the operational cost.
4. **If the channel gets expensive, stop mass sending.** Emailing everyone breaks even at **$0.31 per contact** (at 40% margin). At $0.50 per contact it loses $0.19 per customer versus sending nothing.

## Evidence

| Policy (40% margin, $0.10 per email) | Profit per customer | 95% CI |
|---|---|---|
| Send nothing | $0.261 | $0.200 to $0.327 |
| Women's email to everyone | $0.331 | $0.248 to $0.413 |
| **Men's email to everyone** | **$0.469** | $0.368 to $0.557 |
| Match the email to past purchases | $0.471 | $0.374 to $0.563 |
| Uplift model (gradient boosting) | $0.482 | $0.386 to $0.575 |

Customers were randomly assigned to no email, the men's email or the women's email, so these differences are causal. Group sizes match a three-way split (p = 0.90) and no customer trait differs between groups by more than 0.01 standard deviations.

**Two findings that change what to do:**

- **Matching the email to past purchases is no better than sending the men's email to all.** Customers who bought only men's products barely respond to the women's email (visit-rate lift of 1.1 points, against 6.9 for the men's email). Customers who bought only women's products respond about equally to both (7.4 and 7.1 points).
- **Customers who bought both lines are the most valuable segment.** Their visit rate rises by 13.4 points under the men's email, against 7.0 points for everyone else (p < 0.001). Their spend lift is the largest, at $1.82 per customer, though the spend difference from other groups is not statistically clear (p = 0.09).

## Why this matters beyond this test

When we engineered a realistic non-random rollout (loyal customers more likely to be emailed), the naive comparison **overstated the spend lift by 58%** and the visit lift by 67%. Matching, weighting and doubly robust methods recovered the true effect to within about 6% on spend and 1% on visits. If past campaign reports compare emailed customers with everyone else, their ROI figures are probably inflated by a similar margin. Keeping a random holdout group on every campaign removes the problem.

## Assumptions and limits

- **Margin and cost are assumptions.** The data has no cost or margin figures. I used a 40% margin and $0.10 per email, and tested margins of 20% to 60% and costs of $0.05 to $2.00. The conclusion to email everyone holds whenever cost per contact is below roughly $0.31 at 40% margin.
- **Short window.** Spend covers two weeks after the email. Repeat purchases, unsubscribes and list fatigue are not measured.
- **Few conversions.** Only about 580 customers converted, which is why individual-level targeting cannot be separated from noise and why segment findings should be treated as leads to confirm.
- **One campaign, an older list.** Results may not transfer to other products, seasons or audiences.

## Next steps

1. Roll out the men's email to the full list, keeping a 5% random holdout to measure ongoing lift.
2. Run a follow-up test of the bought-both segment against a larger sample, especially if a costlier channel (direct mail, a discount offer) is on the table. At $0.50 per contact, targeting only this group is the one promising option (+$0.024 per customer, interval -$0.029 to +$0.074).
3. Revisit the uplift model once the next test provides several times more conversions.

*Figures: `outputs/` holds the full results; the interactive dashboard in `dashboard/` lets you change margin, cost and audience size.*
