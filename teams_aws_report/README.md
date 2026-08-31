# Grafana-to-Teams Image Reporter

This script downloads a rendered Grafana dashboard image and sends it to a
Microsoft Teams channel through a preconfigured Teams Workflow Webhook:

Grafana Render URL
        ↓
   Python script
        ↓
Adaptive Card JSON
        ↓
Teams Workflow Webhook
        ↓
Teams Channel

## 1. Installation

Windows:

```powershell
py -m pip install -r requirements.txt
```

Linux:

```bash
python3 -m pip install -r requirements.txt
```

## 2. Create the Configuration File

Copy the example configuration:

```bash
cd teams_aws_report
cp config.example.json config.json
```

Edit `config.json` and replace the placeholder values:

```json
{
        "grafana_render_url": "https://grafana.example.com/render/d-solo/...",
        "teams_webhook_url": "https://your-teams-workflow-webhook-url",
        "title": "Grafana Dashboard Report",
        "image_timeout_seconds": 30,
        "webhook_timeout_seconds": 30,
        "retries": 4
}
```

`grafana_render_url` must point to the Grafana Render endpoint that returns an
image. Include the dashboard, panel, time range, and image dimensions in the
URL as required by your Grafana setup.

The Teams Workflow must be configured to accept an incoming webhook and post
the received Adaptive Card to the target channel. The Webhook URL is stored
only in the local `config.json` file.

## 3. Run the Reporter

```bash
python teams_report.py
```

The script performs these steps:

1. Loads `config.json`.
2. Downloads the image returned by `grafana_render_url`.
3. Encodes the image as a Base64 data URI.
4. Builds an Adaptive Card with the image and current timestamp.
5. Sends the card to `teams_webhook_url`.

The script retries HTTP 429 and transient HTTP 5xx responses. It does not
query AWS Cost Explorer or require AWS credentials.

## 4. Grafana Render URL

Grafana's Render API commonly uses a URL similar to:

```text
https://grafana.example.com/render/d-solo/<dashboard-id>/<slug>?orgId=1&panelId=1&width=1600&height=900&from=now-24h&to=now
```

If Grafana requires authentication, configure the Render endpoint and access
method so that this script can download it. Authentication headers can be
added to the JSON configuration later if needed.

The rendered image is embedded in the Adaptive Card before it is sent. Keep
the image dimensions and file size reasonable because Teams Workflow messages
have payload size limits.

## 5. Schedule Execution

On Windows, use Task Scheduler.

On Linux, use cron. For example, to run every day at 09:00:

```cron
0 9 * * * cd /opt/teams/teams_aws_report && /usr/bin/python3 teams_report.py
```

To run every five minutes:

```cron
*/5 * * * * cd /opt/teams/teams_aws_report && /usr/bin/python3 teams_report.py
```

## 6. Security Notes

The Webhook URL is a credential. Do not commit `config.json` to Git. It is
already included in `.gitignore`; commit only `config.example.json`.

If the Webhook URL is exposed, regenerate or update it in the Teams Workflow.

# AWS Cost Report: Grafana to Teams

This project queries Grafana through a service-to-service token, renders the
query results with an external Jinja template, and posts a compact Adaptive
Card to a Microsoft Teams Workflow.

The report contains text only. Grafana images are not embedded, which keeps
the Teams request below the 28 KB limit.

## Project Structure

```text
teams_aws_report/
        teams_report.py                 Application entry point (version 0.2)
        grafana_client.py               Grafana HTTP API client
        query_runner.py                 Pluggable query execution layer
        aws_ec2.py                      AWS EC2 inventory via boto3
        queries/*.sql                    Independent Athena SQL files
        templates/aws_cost_report.md.j2 Report template
        config.example.json             Configuration example
        config.json                     Local configuration, never commit this
```

## Installation

```bash
cd teams_aws_report
python3 -m pip install -r requirements.txt
```

## Configuration

Copy the example file:

```bash
cp config.example.json config.json
```

The config file only holds the static structure and the `enabled` switches.
Everything that changes per environment is read from environment variables,
so you never have to edit `config.json` to move between environments:

```bash
# Grafana
export GRAFANA_BASE_URL="https://grafana.example.com:3000"   # required
export GRAFANA_S2S_TOKEN="your-grafana-service-account-token"
# Teams
export TEAMS_WEBHOOK_URL="your-teams-workflow-webhook-url"
# AWS (only needed when you use an "ec2" query section)
export AWS_ACCESS_KEY_ID="your-aws-access-key-id"
export AWS_SECRET_ACCESS_KEY="your-aws-secret-access-key"
export AWS_REGION="us-east-1"
# Optional
export REPORT_TITLE="Daily Ops Report"          # default: Daily Ops Report
export EC2_BUDGET_TAG="budgetcode"              # default: budgetcode
export DATASOURCE_ATHENA_UID="your-athena-uid"  # per datasource, see below
export DATASOURCE_OPENOBSERVE_UID="your-openobserve-uid"
```

`config.json` then only defines the report structure and switches:

```json
{
        "debug": false,
        "grafana": { "token_env": "GRAFANA_S2S_TOKEN", "timeout_seconds": 30 },
        "teams": { "webhook_url_env": "TEAMS_WEBHOOK_URL", "timeout_seconds": 30, "retries": 4 },
        "aws": {
                "access_key_env": "AWS_ACCESS_KEY_ID",
                "secret_key_env": "AWS_SECRET_ACCESS_KEY",
                "region_env": "AWS_REGION"
        },
        "template": "templates/aws_cost_report.md.j2",
        "datasources": [
                { "name": "athena", "model": { "format": "table", "rawQuery": true } },
                { "name": "openobserve", "model": { "format": "table" } }
        ],
        "sections": []
}
```

Datasource UIDs come from `DATASOURCE_<NAME>_UID` env vars (the datasource
`name` uppercased, dashes become underscores), for example
`DATASOURCE_ATHENA_UID`. The report title comes from `REPORT_TITLE`.

The Grafana service account needs permission to query the selected datasources.
The Teams Workflow URL is treated as a secret and must not be committed.
The AWS credentials are read from the environment variables named in the
`aws` section and are never stored in `config.json`.

## AWS EC2 inventory

An EC2 section queries the AWS account directly through the SDK (no Grafana
datasource) and lists instances grouped by a budget tag:

```json
{
        "title": "EC2 Inventory",
        "queries": [
                { "name": "ec2_by_budgetcode", "type": "ec2", "untagged_label": "none" }
        ]
}
```

For each instance it shows
`Name | InstanceId | InstanceType | State`, then a summary:

```text
Total: 42 (30 running)
alpha: 10 (8 running)
none: 3 (1 running)
[alpha]
web-1 | i-0abc123 | t3.micro | running
...
[none]
db-1 | i-0xyz789 | t2.nano | running
- | i-0def456 | c5.xlarge | running
```

Instances **without** the budget tag are not dropped: they are grouped under
`untagged_label` (default `none`) so you can spot and tag them. The group
appears last in the report. Each instance also includes its `Name` tag (shown
as `-` when the instance has no name).

The `type: "ec2"` query uses the `aws` section for credentials and the
`ec2_budget_tag` (default `budgetcode`) to filter instances. Credentials are
resolved from the environment (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_REGION`). If no `aws` section is present, EC2 queries fail with a clear
error instead of silently returning empty data.

## Debugging

Set `"debug": true` in `config.json` (or export `TEAMS_AWS_DEBUG=1`) to print
extra diagnostics to stderr:

- The exact URL and JSON payload sent to Grafana's `/api/ds/query`.
- The raw response body returned by Grafana.
- The webhook URL, attempt number, and payload size for each Teams request.
- A full Python traceback when the script fails.

On any failure, the HTTP status code and the API response body are always
printed, even when debug is disabled. Keep `debug` off in production to avoid
logging sensitive values such as query payloads.

## Datasources and sections

The report is composed of **datasources** (defined once, referenced by name)
and **sections** (each a titled group of queries). This keeps the datasource
UIDs in one place and lets a query pick any datasource.

### 1. Define the datasources

Each entry has a `name` (used to reference it) and the Grafana `uid`. A
datasource may also carry default `model` fields that every SQL query on it
inherits:

```json
"datasources": [
        {
                "name": "athena",
                "uid": "your-athena-datasource-uid",
                "model": { "format": "table", "rawQuery": true }
        },
        {
                "name": "openobserve",
                "uid": "your-openobserve-datasource-uid",
                "model": { "format": "table" }
        }
]
```

### 2. Group queries into sections

Each section has a `title` rendered in the report, plus a `queries` array.
Every query references a datasource **by name** and is one query → one SQL
file (`sql_file`), or carries its whole query in `model` (no `sql_file`):

```json
"sections": [
        {
                "title": "AWS Cost",
                "queries": [
                        { "name": "daily_cost", "datasource": "athena", "sql_file": "queries/daily_cost.sql" },
                        { "name": "ec2_by_budgetcode", "datasource": "infinity", "model": {
                                "type": "json",
                                "url": "https://example.com/api/ec2-instances",
                                "query": "items[] | {instance: .instance_id, code: .tags.budgetcode}",
                                "format": "table"
                        } }
                ]
        },
        {
                "title": "Service Status",
                "queries": [
                        { "name": "http_5xx_ratio", "datasource": "openobserve", "sql_file": "queries/http_5xx_ratio.sql" }
                ]
        }
]
```

Rules:

- **`sql_file` is optional.** For SQL-backed datasources (Athena, PostgreSQL,
  OpenObserve) the file is loaded and inserted into `model.rawSql`. For
  datasources that carry their whole query in the model (for example the
  Infinity or Prometheus plugins), omit `sql_file`.
- **A query's `model` replaces the datasource's default `model` entirely**, so
  a non-SQL query never inherits `rawQuery`/`format: table` from the
  datasource defaults.
- **`display` is optional.** Set `"display": "calendar"` on a query to render
  its daily rows as a compact list (days grouped three per line as
  `DD<weekday> <value>`, for example `01Sat 1962.74`), skipping days that have
  no data, instead of the default `key: value` rows. The example `daily_cost`
  query uses it.
- **`enabled` is optional.** Set `"enabled": false` on a query or on a whole
  section to skip it (JSON has no comments, so this is the way to temporarily
  turn something off without deleting the config). Defaults to `true`.
- The `model` object is sent to Grafana's `/api/ds/query` endpoint. Copy the
  exact query model from the Grafana panel (inspect the network request) when
  a plugin needs datasource-specific fields.

### Example queries

The SQL files under `queries/` are the Athena ones used by the AWS Cost
section:

- `queries/daily_cost.sql`
- `queries/monthly_cost.sql`
- `queries/cost_by_service.sql`
- `queries/cost_by_account.sql`

Edit these files directly to match your Athena tables, columns, and business
rules. The project does not require SQL to be written on one line.

### Tutorial: adding datasources, sections, and custom templates

**1. Add a datasource** — append an entry to the `datasources` array. No code
or template change is needed:

```json
{
        "name": "postgres",
        "uid": "your-postgres-datasource-uid",
        "model": { "format": "table", "rawQuery": true }
}
```

**2. Add a section** — append an entry to the `sections` array. The default
template picks it up automatically and renders `## <title>` followed by each
query:

```json
{
        "title": "Database Health",
        "queries": [
                { "name": "slow_queries", "datasource": "postgres", "sql_file": "queries/slow_queries.sql" }
        ]
}
```

**3. Customize the template (optional)** — if the default `key: value` layout
is not enough, edit `templates/aws_cost_report.md.j2`. It is plain Jinja2, so
no Python changes are required. The template receives:

- `title` — the top-level report title (`config.json` → `title`)
- `generated_at` — a timestamp string
- `sections` — a list, each item is
  `{ "title": "...", "results": { "<query_name>": { "rows": [...], "raw": {...} } } }`

For example, render each query as a Markdown table:

```jinja2
{% for section in sections %}
## {{ section.title }}
{% for name, result in section.results.items() %}
### {{ name }}
{% if result.rows %}
{% set headers = result.rows[0].keys() %}
| {% for h in headers %}{{ h }} |{% endfor %}
|{% for h in headers %}---|{% endfor %}
{% for row in result.rows %}
| {% for value in row.values() %}{{ value }} |{% endfor %}
{% endfor %}
{% else %}
No data returned.
{% endif %}
{% endfor %}
{% endfor %}
```

Because the whole report is rendered by this one template, adding a new
section to `config.json` immediately changes the report — you only touch the
`.j2` file when you want a different layout for its output.

### Formatting dates in SQL

Grafana serializes Athena `DATE`/`TIMESTAMP` columns as epoch milliseconds
(for example `1785542400000`). Format them in SQL so the report shows a
readable value:

```sql
DATE_FORMAT(date(line_item_usage_start_date), '%Y-%m-%d') AS usage_date   -- 2026-08-01
DATE_FORMAT(date_trunc('month', line_item_usage_start_date), '%Y-%m') AS usage_month
```

### Reducing Athena scan cost

Athena bills by bytes scanned. The sample queries already do two things to
keep that low:

- **Partition pruning**: every query filters
  `billing_period = DATE_FORMAT(current_date, '%Y-%m')` so only the current
  month's partition is scanned. Do **not** switch this back to a filter on
  `line_item_usage_start_date` — that is a data column, not a partition, and
  would force a full-table scan.
- **Column pruning**: only the needed columns are selected, so Parquet only
  reads those columns.

`billing_period` values are `YYYY-MM` strings (for example `2026-08`), and the
queries match the current month with `DATE_FORMAT(current_date, '%Y-%m')`.
Confirm the layout with:

```sql
SHOW PARTITIONS athenadataexports_aws_finops_cur.data;
```

Note: this export's Parquet files are currently uncompressed
(`compressionType = 'none'`). Compressing them (for example GZIP/Snappy) at
the export or crawler level would cut scanned bytes further; that is
configured outside of these queries.

### Notes

The sample SQL files have no region filter, so they cover all regions. If you
need region-specific filtering, add a condition such as
`AND product_region_code = 'us-east-1'` to the `WHERE` clause. Prometheus,
Loki, and other datasource models can be added as separate query entries (with
their own `model`) without changing the report workflow.

Query results are converted into rows and are available in the template as:

```text
sections[].title
sections[].results.<query_name>.rows
sections[].results.<query_name>.raw
```

## Template

The default template is `templates/aws_cost_report.md.j2`. It can be edited
without changing Python code. It iterates the configured sections and renders
a `## <section title>` heading followed by the queries in that section. A
simple section looks like this:

```jinja2
{% for section in sections %}
## {{ section.title }}
{% for name, result in section.results.items() %}
### {{ name }}
{% for row in result.rows %}
{{ row.date }}: {{ row.cost }}
{% endfor %}
{% endfor %}
{% endfor %}
```

The template is rendered as plain text inside an Adaptive Card. The script
serializes the complete Teams request and rejects it when its UTF-8 size is
28 KB or larger.

## Run

Run the script from this directory without command-line arguments:

```bash
python3 teams_report.py
```

The workflow is:

1. Load `config.json`.
2. Authenticate to Grafana with the configured S2S token.
3. Execute every configured query.
4. Render the external Jinja template.
5. Validate the Teams payload size.
6. Post the report to the Teams Workflow.

## Schedule

For a five-minute schedule with cron:

```cron
*/5 * * * * cd /opt/teams_aws_report && /usr/bin/python3 teams_report.py
```

## Extending with more datasources

Every datasource in `config.json` is queried through Grafana's `/api/ds/query`
endpoint, so adding an Athena, Infinity, OpenObserve, Prometheus, or any other
Grafana datasource is just a new entry in the `datasources` array plus the
queries that reference it. A query's `model` is sent verbatim, so copy the
datasource-specific model fields from the Grafana panel's query inspector when
needed.

## Security

`config.json` is ignored by Git. Keep both the Grafana token and Teams Webhook
URL outside tracked files. If either credential is exposed, rotate it at its
source.
