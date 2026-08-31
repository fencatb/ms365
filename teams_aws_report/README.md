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
        teams_report.py                 Application entry point
        grafana_client.py               Grafana HTTP API client
        query_runner.py                 Pluggable query execution layer
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

Set the secrets in the environment:

```bash
export GRAFANA_S2S_TOKEN="your-grafana-service-account-token"
export TEAMS_WEBHOOK_URL="your-teams-workflow-webhook-url"
```

Then edit `config.json`:

```json
{
        "debug": false,
        "grafana": {
                "base_url": "https://grafana.example.com",
                "token_env": "GRAFANA_S2S_TOKEN",
                "timeout_seconds": 30
        },
        "teams": {
                "webhook_url_env": "TEAMS_WEBHOOK_URL",
                "timeout_seconds": 30,
                "retries": 4
        },
        "title": "AWS Cost Report",
        "template": "templates/aws_cost_report.md.j2",
        "queries": []
}
```

The Grafana service account needs permission to query the selected datasource.
The Teams Workflow URL is treated as a secret and must not be committed.

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

## Grafana Queries

Each query loads its SQL from a separate file. The example configuration
contains four queries, each backed by one SQL file:

- `queries/daily_cost.sql`
- `queries/monthly_cost.sql`
- `queries/cost_by_service.sql`
- `queries/cost_by_account.sql`

Edit these SQL files directly to match your Athena tables, columns, and
business rules. The project does not require SQL to be written on one line.

### Adding a new query

Shared settings live in the `query_defaults` section, so a new query only
needs a `name` and a `sql_file`. Every entry is one query → one SQL file:

```json
"query_defaults": {
        "provider": "grafana",
        "datasource_uid": "your-athena-datasource-uid",
        "model": {
                "format": "table",
                "rawQuery": true
        }
},
"queries": [
        { "name": "daily_cost", "sql_file": "queries/daily_cost.sql" },
        { "name": "cost_by_service", "sql_file": "queries/cost_by_service.sql" }
]
```

To add a fifth query, create `queries/my_new_query.sql` and add one entry:

```json
{ "name": "my_new_query", "sql_file": "queries/my_new_query.sql" }
```

Per-query keys override `query_defaults`. The nested `model` dict is merged
field-by-field, so you can, for example, set a per-query `refId` without
repeating `format` and `rawQuery`. The `model` object is sent to Grafana's
`/api/ds/query` endpoint, and the selected SQL file is inserted into
`model.rawSql` at runtime.

The datasource UID is read from `query_defaults` (or overridden per query).
For Athena, use the UID of the Athena datasource configured in Grafana. All
queries can share the same UID when they query the same datasource.

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
results.<query_name>.rows
results.<query_name>.raw
```

## Template

The default template is `templates/aws_cost_report.md.j2`. It can be edited
without changing Python code. A simple section looks like this:

```jinja2
## Daily Cost
{% for row in results.daily_cost.rows %}
{{ row.date }}: {{ row.cost }}
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

## Extending Query Providers

The current provider is `grafana`. To add another source later, implement a
handler in `query_runner.py` and register it in `QUERY_HANDLERS`. Templates
will continue to receive the same `{ "name", "rows", "raw" }` result shape.

## Security

`config.json` is ignored by Git. Keep both the Grafana token and Teams Webhook
URL outside tracked files. If either credential is exposed, rotate it at its
source.
