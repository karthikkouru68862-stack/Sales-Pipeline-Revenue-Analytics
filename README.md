# Sales Pipeline & Revenue Analytics — Flexible Data Analytics Website

SalesPulse is a role-based Flask analytics website. It supports CRM/sales analysis when those fields exist, and general exploratory analysis for any CSV dataset. **No particular column names or sales fields are required.**

## Main capabilities

- No Pandas or NumPy dependency. The backend uses Python's standard `csv`, `datetime`, `statistics`, and `sqlite3` modules. Flask is the only Python package.
- **Flexible CSV upload:** any readable CSV with a header row and at least one non-empty data row can be uploaded. There is no required revenue, date, product, customer, status, or other business column. Blank/duplicate headers are normalized, and extra row values are retained as additional columns.
- **Automatic data profiling:** columns are inferred as numeric, date, categorical, or empty based on their values. The system derives row/column counts, missing-cell and duplicate-row checks, numeric totals/averages/medians/ranges, category breakdowns, numeric distributions, date-based trends, and plain-language insights.
- **Sales/CRM enhancements are optional:** when recognized sales measures or CRM opportunity fields exist, the app can also calculate revenue, pipeline, win/loss, expected revenue, and stage metrics. These KPIs are not fabricated when the uploaded file does not contain supporting data.
- **Raw columns are preserved:** source values are retained so the Data Explorer can display and search all uploaded columns, including fields not recognized as sales fields.
- **Interactive analysis:** click a category bar to cross-filter by that uploaded column, click a month to filter by the detected date column, and click a numeric histogram bin to filter by that value range.
- Role-specific data scope: admin sees all records; salespeople and regional users are scoped when a recognizable salesperson/region field is present. If the file has no suitable access field, non-admin views remain empty rather than exposing all records to an unrelated role.
- Server-side search, CSV export with raw values, login throttling, CSRF protection, and SQLite user storage.

## Windows setup (PowerShell)

Open PowerShell in the extracted project directory:

```powershell
py -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:5000>.

You can also double-click `start_windows.bat`. Only Flask is required by `requirements.txt`.

## Uploading data

1. Sign in as an administrator and open **Upload Data**.
2. Choose a `.csv` file and select **Upload and analyze dataset**.
3. The file must be a readable `.csv` with a header row and at least one non-empty data row. **No particular column names or sales fields are needed.** A completely headerless file cannot be profiled reliably; save the first row as column headings before upload.
4. SalesPulse keeps all original columns and values, profiles the data, and builds visualizations based on what it detects.
5. When sales or CRM columns are recognizable, additional sales-specific measures are enabled. Otherwise, the dashboard switches to the general Data Insights view.

### Examples of supported files

A dataset with only text categories is valid:

```csv
Team,Shift,Feedback
Support,Morning,Positive
Sales,Evening,Neutral
Operations,Morning,Positive
```

A dataset with numbers and dates is valid:

```csv
Period,Department,Score,Hours
2026-01,North,72,10
2026-02,South,89,12
2026-03,North,80,11
```

A standard sales dataset is also valid:

```csv
Order Date,Product Name,Customer Name,Territory,Quantity,Unit Price,Sales
2026-01-10,CRM Software,ABC Ltd,South,2,25000,50000
2026-01-11,Analytics Suite,XYZ Ltd,West,1,42000,42000
```

A CRM opportunity pipeline dataset can activate pipeline-specific metrics:

```csv
Opportunity ID,Created Date,Sales Rep,Account Name,Region,Product,Deal Amount,Sales Stage,Win Probability,Expected Close Date,Status
OP-101,2026-01-05,Arun,ABC Ltd,South,CRM,500000,Negotiation,80%,2026-03-15,Open
OP-102,2026-01-10,Priya,XYZ Ltd,West,Analytics,300000,Proposal,60%,2026-03-20,Open
```

Only `.csv` files are currently supported in this release. Save Excel workbooks as CSV before uploading. Maximum file size: 15 MB. The previous dataset is backed up to `data/sales_pipeline_backup.csv` before replacement.

## Generic insight interpretation

- Numeric columns provide count, total, mean, median, minimum, maximum, and interactive value-range histograms.
- Categorical columns provide top-value breakdowns; when a numeric field exists, categories are ranked by the sum of the selected numeric field.
- Date columns provide month-by-month trends, using a numeric sum when available or record counts when no numeric column exists.
- Text-only files are still useful: the app profiles distinct values, blank cells, duplicates, and common categories.
- The system does not invent revenue or CRM KPIs if the file does not contain supporting fields. Generic fields named `Stage` or `Status` alone are not enough to classify a file as a CRM pipeline; the detector also looks for deal values, probabilities, or other CRM identifiers.

## Security and deployment

- Every POST form is protected by a session CSRF token.
- Login attempts are throttled after five failures for a username/IP pair during the configured time window.
- The seeded admin account is forced to change the published starter password on first login. Change all demo credentials before deployment.
- Set a strong `SECRET_KEY` environment variable before direct production startup. The app refuses to start with debug disabled if it is missing.
- Use HTTPS and a production WSGI server before exposing the app publicly.

Generate a local secret with:

```powershell
py -c "import secrets; print(secrets.token_hex(32))"
```

Then set it for the current PowerShell session:

```powershell
$env:SECRET_KEY = "paste-a-long-random-secret-here"
.\venv\Scripts\python.exe app.py
```

## Project structure

```text
Sales_Pipeline_Revenue_Analytics_Web/
├── app.py
├── requirements.txt
├── README.md
├── CHANGELOG.md
├── start_windows.bat
├── data/
│   ├── sales_pipeline_clean.csv
│   ├── sample_simple_sales.csv
│   ├── sample_crm_pipeline.csv
│   ├── sample_general_dataset.csv
│   └── dataset_meta.json
├── instance/                      # SQLite user database created at first run
├── templates/
│   ├── base.html
│   ├── welcome.html
│   ├── login.html
│   ├── _filters.html
│   ├── _generic_insights.html
│   ├── dashboard.html
│   ├── pipeline.html
│   ├── revenue.html
│   ├── opportunities.html
│   ├── upload.html
│   └── error.html
└── static/
    ├── style.css
    └── app.js
```


## Temporary uploaded datasets and restoring the starter data

Administrator CSV uploads are stored separately from the bundled starter dataset. After a successful upload, the Overview page analyzes the uploaded file. The Overview page displays an **Remove uploaded data** action for administrators. Using it deletes only the active upload and its metadata, clears the in-memory data cache, and immediately restores the original bundled dataset and its insights for all users.

The protected baseline files are `data/original_sales_pipeline.csv` and `data/original_dataset_meta.json`. New uploads are stored in `data/active_uploaded_dataset.csv` and `data/active_uploaded_meta.json`. Do not manually replace or delete the protected baseline files if you want to retain the original starter dataset.
