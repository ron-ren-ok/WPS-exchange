# Winriser partner sync

Reads the Tracker / EntireTrack `Daily Install Report` as user `WPS`, keeps
only `Source = WPS`, and writes the two bubble metrics into the live headers
`Winriser气泡新增` and `Winriser气泡血量` in `合作方返回数据`.

The job intentionally has no popup mapping. Tracker is the authoritative
source for Winriser bubble data only.

## GitHub Actions secrets

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON` (already used by the other jobs)
- `WINRISER_LOGIN_SECRET` (the value after `WINRISER_LOGIN_SECRET=` in the
  local credential file; do not include quotes or the variable name)

The scheduled workflow uses the site's `Last Week` report and writes through
the previous Asia/Shanghai calendar day. Use `workflow_dispatch` with an
explicit start date only when that date is within the report window.
