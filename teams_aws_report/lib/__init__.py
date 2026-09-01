"""Reusable libraries for the Grafana-to-Teams report tool.

Keep the entry point (``teams_report.py``) thin and put independent, reusable
units here so new features can import them cleanly:

- ``config``          Config file loading + environment resolution
- ``template``        Jinja rendering and display filters (e.g. ``calendar``)
- ``teams``           Adaptive Card building and Teams webhook delivery
- ``grafana_client``  Grafana HTTP API client
- ``query_runner``    Query execution layer
- ``aws_ec2``         AWS EC2 inventory via boto3
"""
