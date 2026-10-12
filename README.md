# Kaggle T4 x2 Queue Monitor

Monitor Kaggle GPU allocation across four accounts with one randomized probe per
account in every 30-minute window. Private Vietnamese dashboard, persistent
SQLite collector and Docker deployment.

Dashboard: **https://vm-manager-thangma-z97m-d3h.tailf1bdc4.ts.net:8443**
(authorized Tailscale devices only).

See [deployment and operation instructions](monitor/README.md).
Roadmap: https://github.com/mathangspk/kaggle_test_waiting/issues/10

Queue duration is an observational bound. Submit-to-start latency is a separate
metric. <=5 minutes is acceptable; >10 minutes unacceptable. Automated queued
cancellation is blocked until exact native session identity can be resolved and
provider cancellation verified. See issue #4.

Credentials, runtime databases and private SSH keys are excluded from Git.
