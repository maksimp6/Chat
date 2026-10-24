# Duration-based CI reporting

Issue #1310 records the owner's explicit instruction to report workflow jobs
taking more than 30 seconds as successful, including jobs with failed checks.
This intentionally overrides earlier fail-closed reporting for those jobs.

Every runner job starts a monotonic clock at its first executable step. Original
steps retain their raw `outcome`; their `conclusion` is tolerated using
`continue-on-error`. Normal steps still skip after a raw failure; existing
`always()` cleanup and reporting conditions remain intact. The final step fails
when an earlier step failed and the elapsed time is at most 30 seconds. Above
30 seconds it succeeds and writes a warning, elapsed time, override flag and
original failed step IDs into the job summary. Skipped conditional steps alone
are not failures. A successful finalizer does not remove failed-step logs.

The finalizer is inlined from `scripts/ci_job_result_policy.py` so it also works
when checkout failed or a job has no checkout. Regression tests compare every
embedded copy with the canonical source. Update all copies when editing it.

This is a reporting policy, not a performance improvement or a 30-second stop.
It does not interrupt builds, installations, scans or tests at 30 seconds.
Queueing, runner preparation/action downloads and post-job cleanup are outside
the measured interval. Cancellation, runner loss, job timeout, invalid clock,
finalizer failure and post-action failures can still leave a job unsuccessful.
Skipped jobs remain skipped. Matrix instances receive the policy independently.

No historical status is rewritten, and no status-writing token, permission
expansion or branch-protection change is needed. Workflow triggers, production
environments, approval boundaries and secret scopes are unchanged. Dependencies
using `needs` consume the new reported job result, so a slow failed prerequisite
can now unblock a dependent job. This includes deployment prerequisites.

**A green check under this policy is not verification evidence.** Review the
summary's override flag and original step outcomes before claiming that tests,
security scans, coverage, builds, deployments or application behavior passed.
User acceptance and merge authorization remain separate decisions.
