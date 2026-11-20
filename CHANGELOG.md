# Corrections in this release

## Data ingestion and filtering
- Transaction rows now preserve their order/sale date in `Actual_Close_Date`; date range and month-click filters share one effective-date function.
- Cancelled, canceled, refunded, failed, rejected, void, chargeback, declined, returned, and lost transaction rows are excluded and counted in the upload result.
- Number parsing supports European decimal-comma formats (for example `1.234,50` and `100,00`) as well as US formats.
- Revenue KPIs and revenue charts use a filter scope that ignores Stage and Status selections, while pipeline/outcome views retain those selections.
- Doughnut chart metric labels match the displayed values (Revenue vs. Records).
- The funnel uses the stage labels returned by the uploaded data, including custom CRM stages.
- The bundled dataset has no future-dated closed deals. Open opportunities past their expected close date are flagged separately and excluded from active pipeline value/expected revenue.
- Missing expected-close dates are not automatically classified as overdue.
- Opportunity CSV export contains raw numeric values. Search is applied server-side across the full filtered dataset without a 1,000-row cap.
- Salesperson account mappings are checked after upload and mismatches are reported to the admin.
- Region revenue cards are rendered in the revenue page.
- The CSV cache is reused between requests; cached rows are treated as immutable and copied only for response augmentation.

## Security and deployment
- All POST forms use session CSRF tokens.
- Login attempts are throttled after five failed attempts per username/IP pair for 15 minutes.
- The seeded admin account must change its starter password at first sign-in.
- Production startup requires `SECRET_KEY`; no hardcoded production secret is included.
- Login `next` redirects are validated as local paths to block external and backslash-based redirects.
- Chart.js CDN usage was removed; local SVG charts work without an external chart script.

## Documentation and metadata
- README explains CRM-vs-transaction detection, the effective date field, non-sale row exclusions, and overdue pipeline handling.
- Bundled dataset metadata identifies its source as “Bundled starter dataset”; the source label is not displayed as an upload date.


## Flexible any-column data upload

- Removed the sales-column requirement: any CSV with headers and data rows can be uploaded.
- Preserved every uploaded source field as raw data for profiling and exploration.
- Added automatic numeric/date/categorical profiling, missing/duplicate checks, numeric summaries, category comparisons, numeric histograms, date trends, and generated findings.
- Added interactive category, month, and numeric-range cross-filtering.
- Updated the Data Explorer table to show all source columns.
- Kept sales and CRM KPIs optional and only enabled when the uploaded data supports them.


## Dataset-agnostic upload refinements

- Generic tables that happen to contain `Stage` and `Status` columns are no longer classified as CRM pipelines based on those common headers alone. CRM detection requires a deal-value/probability signal or multiple additional deal/owner/account identifiers.
- Simplified duplicate frontend analytics error messaging.
- Clarified that the current uploader accepts arbitrary-column CSV files (including blank/duplicate headers and ragged rows), while Excel files must be saved as CSV first.


## Dataset switching improvement
- Added a protected, immutable copy of the bundled starter dataset and metadata.
- Administrator uploads now write to separate active-upload files and never overwrite the starter dataset.
- Added an administrator-only **Remove uploaded data** action to the Overview page.
- Removing an upload clears its data and metadata, resets the cache, and restores the original dataset and insights for every dashboard.
- Successful uploads now redirect to Overview so the newly uploaded dataset insights are shown immediately.
- Added migration logic for older installs where the previous version replaced the starter CSV and created a backup.
