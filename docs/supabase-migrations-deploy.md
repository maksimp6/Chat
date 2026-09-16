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
