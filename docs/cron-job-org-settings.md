# cron-job.org settings template

GitHub's own `schedule:` is best-effort and was skipping runs.
cron-job.org now triggers `levels.yml` through `workflow_dispatch`.
Create ONE cron-job.org job per timeframe (6 jobs). Timezone: UTC.

## Shared request settings (same for all 6 jobs)

| Setting | Value |
|---|---|
| URL | https://api.github.com/repos/smart-damian/SMART-LAB-3/actions/workflows/levels.yml/dispatches |
| Method | POST |
| Header | Authorization: Bearer <YOUR_TOKEN> |
| Header | Accept: application/vnd.github+json |
| Header | Content-Type: application/json |
| Success | HTTP 204 |
| Notify on failure | ON (email) |

## Per-job settings

| Job title | Body | Schedule (UTC) |
|---|---|---|
| levels-5m | {"ref":"main","inputs":{"timeframe":"5m"}} | every 5 min |
| levels-15m | {"ref":"main","inputs":{"timeframe":"15m"}} | minutes 4,19,34,49 |
| levels-30m | {"ref":"main","inputs":{"timeframe":"30m"}} | minutes 1,31 |
| levels-1h | {"ref":"main","inputs":{"timeframe":"1h"}} | minute 2 |
| levels-4h | {"ref":"main","inputs":{"timeframe":"4h"}} | 00:03, 04:03, 08:03, 12:03, 16:03, 20:03 |
| levels-1d | {"ref":"main","inputs":{"timeframe":"1d"}} | 00:06 |

The minute offsets (4, 1, 2, 3, 6) match the old `cron:` lines in
levels.yml so they stay a few minutes after each candle closes.

## GitHub token (fine-grained)

- Repository access: only SMART-LAB-3
- Permission: Actions = Read and write (nothing else)
- Expiry: 90 days. Reminder: replace it by day 80.
- NEVER commit the token. It lives only in cron-job.org headers.

## Test from Termux

    export GITHUB_TOKEN='<YOUR_TOKEN>'   # type it, don't save it in a file
    curl -i -X POST \
      -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      -d '{"ref":"main","inputs":{"timeframe":"1d"}}' \
      https://api.github.com/repos/smart-damian/SMART-LAB-3/actions/workflows/levels.yml/dispatches

Expect `HTTP/2 204`. Then check the Actions tab for a run with event
`workflow_dispatch`.

## Troubleshooting

| Status | Meaning |
|---|---|
| 401 | Token expired or mistyped. Create a new one. |
| 403 | Token lacks Actions: Read and write. |
| 404 | Wrong URL, or the token can't see the repo. |
| 422 | Bad body. Check `ref` and the timeframe spelling. |
