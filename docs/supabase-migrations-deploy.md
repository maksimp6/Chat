# Production Supabase migrations

The workflow `.github/workflows/supabase-migrations.yml` applies committed SQL migrations after pushes to `master` and can also be started manually.

## Required configuration

Configure a GitHub **production environment** with:

- Secret `SUPABASE_ACCESS_TOKEN`: a Supabase access token with permission to link and migrate the project.
- Variable `SUPABASE_PROJECT_ID`: the production Supabase project reference.

Do not commit tokens, database passwords, or connection strings.

## Behaviour

- Runs serially through a concurrency group to avoid overlapping migrations.
- Checks that the migration directory contains SQL files.
- Links the CLI to the configured project.
- Runs `supabase db push --yes`.
- Prints the resulting migration state.

The workflow is intentionally limited to `master`, which is the current production branch. Review the workflow and environment protection rules before enabling it for a live database.

## Recovery after a failed migration

A failed migration must be investigated from the failed GitHub Actions run before another deployment. Do not rewrite or delete an already-applied migration. Create a new corrective migration, validate it in a non-production environment, and then merge it to `master`. The workflow serializes production runs, so a failed run does not start a second migration concurrently.

For an unrecoverable schema change, restore the database from the configured Supabase backup/recovery mechanism and then reconcile migration history before resuming deployments. The repository intentionally uses forward corrective migrations rather than an automatic SQL rollback, because arbitrary database changes cannot safely be reversed generically.
