# Confirmed first report definitions

Approved column metadata is now available for eight tables: `tdriver`, `tvehicle`,
`trip`, `customer`, `tripinvoice`, `tripinvoiceitem`, `users`, and `maintenance`.
There are 189 approved columns across these tables. Maintenance has 24 approved
columns; only branchcode and branchname remain drafts. Its costs are confirmed in
INR, its odometer unit is kilometres, and its status/priority/type codes are
reviewed. Its SQL example remains a draft pending execution and expected-result
checks. Other in-scope tables can be reviewed when a report needs them.

All seven primary metrics for R1-R5 are verified business definitions in
[metrics.yaml](metrics/metrics.yaml). Each of the six monetary metrics also has
a verified companion missing-value count, giving 13 metric entries in total.
These definitions reflect the owner confirmations on 2026-10-13. No business-rule
decisions for this first report set remain pending. Query validation, expected
result checks, and execution are separate work; this review changes no ERP data.

## First reports and review status

| Report | Example question | Confirmed stored facts and date basis |
|---|---|---|
| R1: Completed trips | How many trips completed this month? | Verified definition: count active, non-cancelled trip records with `enddate` in the requested period. |
| R2: Trip amounts | What are the total stored revenue, cost, and profit for trips this month? | Sum known `trip.revenue`, `trip.tripcost`, and `trip.profit` once per selected trip. Filter/group by `enddate` and show a separate missing count for each amount. |
| R3: Issued invoice totals | What is the total invoice amount issued this month? | Sum known `tripinvoice.totalamount` once per invoice, using `issuedate`; include `pending`, `paid`, and `overdue`. Show the missing-totalamount count. |
| R4: Customer balances | What invoice balance does each customer currently owe? | Sum positive stored balances by invoice customer for `pending`, `paid`, and `overdue` invoices. Show missing-balance counts from the eligible invoices before positive-balance filtering. This is a current snapshot. |
| R5: Invoices marked overdue | What is the current stored balance of invoices marked overdue? | Select stored status `overdue`, sum positive stored balances, and show the missing-balance count from the eligible overdue invoices before positive-balance filtering. |

The R4 customer group comes from
`tripinvoice.customer -> customer.customerid`.

## Report rules confirmed on 2026-10-13

1. **Only active, non-cancelled records:** apply `active = 1 AND cancel = 0`
   to the source rows in `trip` and `tripinvoice` for this report batch. Rows with
   NULL in either flag do not match these conditions. These filters do not imply
   a mapping between invoice status codes and the cancel flag, and do not add
   filters to joined customer, driver, vehicle, or user records. User/company
   authorization is a separate implementation concern.
2. **Actual completion date for trip reporting:** use `trip.enddate` for both
   period filters and date grouping in R1/R2, with the confirmed Asia/Kolkata
   date semantics. Trips without an actual completion date are outside these
   completion-date reports. Scheduled departure and record creation are not
   fallback dates. R3 uses invoice issue date.
3. **Invoice statuses:** R3/R4 include only `pending`, `paid`, and `overdue`.
   Draft and cancelled statuses are excluded even when the active/cancel flags
   would otherwise admit the record. R5 narrows this to stored status `overdue`.
4. **Missing monetary amounts:** sum known amounts and display a missing-value
   count beside each monetary result. NULL amounts are not substituted with zero.
5. **Outstanding balances:** R4/R5 include only stored `balancedue > 0` in their
   monetary sums. Zero and negative balances contribute no outstanding amount.

The exact numeric active/cancel predicates are recorded under `required_filters`
for every metric. Invoice status filters and positive-balance filters are also
explicit. Missing-count metrics intentionally omit the positive-balance filter.
The date basis is documented in the metric descriptions/notes; enforcing date
filters and grouping in generated SQL is later work.

## Required missing-value counts

Each monetary metric must be presented with its corresponding
`<metric_name>_missing_count` entry. The count expression is
`COUNT(*) - COUNT(amount_column)` at the same source-row level. Apply the same
record scope, period, and grouping as the amount metric, with one exception:
balance missing counts are evaluated **before** `balancedue > 0` is applied.
An invoice with a NULL balance cannot pass that predicate; counting afterward
would hide missing balances.

For example, eligible stored balances of `100`, `0`, `-20`, and `NULL` produce
an outstanding total of INR 100 and a missing-balance count of 1. Invoices outside
the approved active/cancel/status scope contribute to neither result.

A SUM stays NULL when no known amount contributes; the report must not present
that as a measured zero. A scope with only NULL amounts has a positive missing
count; an empty scope has a missing count of zero. In a balance report, a scope
with only zero/negative known balances also has no qualifying positive total.
Missing-only customer groups must remain visible as data-quality information
even if no positive-balance row is available for that customer.

The approved primary metrics and their mandatory companion counts are:

| Primary monetary metric | Missing-value count metric |
|---|---|
| `stored_trip_revenue` | `stored_trip_revenue_missing_count` |
| `stored_trip_cost` | `stored_trip_cost_missing_count` |
| `stored_trip_profit` | `stored_trip_profit_missing_count` |
| `invoice_billed_total` | `invoice_billed_total_missing_count` |
| `invoice_balance_due` | `invoice_balance_due_missing_count` |
| `overdue_invoice_balance` | `overdue_invoice_balance_missing_count` |

Report generation must combine these results without multiplying source rows
through joins. Companion requirements and date semantics are documented in the
metric notes; the future query planner must enforce them.

## Definitions already confirmed

- Currency is INR. Financial amounts are read from their stored columns. Report
  totals aggregate stored amounts; they do not recalculate individual revenue,
  cost, profit, tax, or balance amounts.
- Trip revenue, trip cost, and trip profit are stored before trip completion.
  Their estimated/final status and later update behavior remain unspecified.
- `tripstarttime` is the stored combination of scheduled `startdate` and
  `starttime`. `tripendtime` combines actual-completion `enddate` and `endtime`.
- Date handling uses `Asia/Kolkata`. Text audit timestamps use the reviewed
  day-month-year format and need parsing for chronological comparisons.
- `tripinvoice.paymentdate` is the **first payment date**. It cannot date every
  later partial payment, so it does not provide a cash-receipts history.
- One invoice can have many items. Invoice totals must be counted once per
  invoice. The schema also permits multiple invoices per trip; trip totals must
  be counted once per trip.
- Item quantities have already been rounded to integers by the backend. Use
  their stored values with the row unit and service description.

The confirmed vocabulary is consolidated in
[business_terms.yaml](glossary/business_terms.yaml). The next step is SQL examples
and expected-result checks for these agreed definitions, including NULL amounts,
zero/negative balances, status exclusions, and invoice-item joins. No SQL examples
are marked verified at this stage. Query validation and execution are later work.

## Format for reviewing a SQL example

Start with this worksheet. You can supply the business question, sample rows,
and expected result; SQL can then be prepared from the agreed rules.

```text
Question:
Reporting period:
Tables:
Date column:
Record/status filters:
Amount or count to return:
Grouping (if any):
Missing-value handling:
Synthetic sample rows:
Expected output:
```

Store query definitions under `queries:` in
[verified_queries.yaml](examples/verified_queries.yaml). Each entry supports
`question`, `sql`, `related_tables`, `tags`, `verified`, `source`,
`reviewed_by`, `reviewed_on`, and `notes`. Sample rows and expected outputs are
documented in this guide; `sample_rows` and `expected_result` are not fields in
the current query-example YAML model.

### Worked example: October invoice total

**Question:** What is the total invoice amount issued in October 2026, including
a count of missing amounts?

- Table: `tripinvoice`.
- Period: 1-31 October 2026, using `issuedate` in the confirmed India timezone.
- Filters: `active = 1`, `cancel = 0`, status in `pending`, `paid`, `overdue`.
- Result: one overall total in INR, plus the number of eligible rows with NULL
  `totalamount`. No grouping or invoice-item join is needed.

All rows below are **synthetic examples**, not rows read from the ERP.

| invoiceid | issuedate | active | cancel | status | totalamount (INR) | Inclusion |
|---|---|---|---|---|---|---|
| 101 | 2026-10-01 | 1 | 0 | pending | 1000.00 | Include |
| 102 | 2026-10-10 | 1 | 0 | paid | 2000.00 | Include |
| 103 | 2026-10-11 | 1 | 0 | overdue | NULL | Include; count missing amount |
| 104 | 2026-10-12 | 1 | 0 | draft | 5000.00 | Exclude: status |
| 105 | 2026-10-13 | 0 | 0 | paid | NULL | Exclude: inactive |
| 106 | 2026-10-14 | 1 | 1 | pending | 9000.00 | Exclude: cancelled |
| 107 | 2026-11-01 | 1 | 0 | paid | 4000.00 | Exclude: outside period |
| 108 | 2026-09-30 | 1 | 0 | paid | 7000.00 | Exclude: outside period |

The corresponding catalog entry is:

```yaml
queries:
  - question: 'What is the total invoice amount issued in October 2026, including a count of missing amounts?'
    sql: |
      SELECT
        SUM(tripinvoice.totalamount) AS invoice_billed_total,
        COUNT(*) - COUNT(tripinvoice.totalamount) AS invoice_billed_total_missing_count
      FROM tripinvoice
      WHERE tripinvoice.active = 1
        AND tripinvoice.cancel = 0
        AND tripinvoice.status IN ('pending', 'paid', 'overdue')
        AND tripinvoice.issuedate >= '2026-10-01'
        AND tripinvoice.issuedate < '2026-11-01';
    related_tables: [tripinvoice]
    tags: [R3, invoice_total, missing_values, synthetic_example]
    verified: false
    source: llm_generated
    reviewed_by: null
    reviewed_on: null
    notes: 'Worked format example using synthetic rows documented in metadata/REPORT_REVIEW.md. Expected output for those rows is invoice_billed_total = 3000.00 INR and invoice_billed_total_missing_count = 1. These are hand-derived expectations, not observed database results. SQL execution and expected-result testing remain pending.'
```

The expected single output row, derived by hand from the synthetic inputs, is:

| invoice_billed_total | invoice_billed_total_missing_count |
|---|---|
| 3000.00 | 1 |

Invoices 101 and 102 contribute INR 1000 + INR 2000. Invoice 103 contributes the
one missing value. Ineligible rows contribute to neither the sum nor the missing
count. The date conditions include 1 October and exclude 1 November.

The entry remains `verified: false` and `source: llm_generated`. Metadata
validation checks its structure and table references; it does not prove the SQL
or result correct. Query validation and a test against synthetic fixtures must
still check the expected result before SQL-example verification.
