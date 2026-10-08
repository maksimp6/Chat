# Database backends — legacy entry point

This path is retained for compatibility with old external links.

The old document described SQLite/PostgreSQL as the common persistence layer for all Alice Pro state. That is no longer the current architecture: durable state is being migrated consumer-by-consumer to file-native Memory DB under #776, while secret values belong to the canonical Secret Store boundary #755.

Use the current documentation instead:

- [Database and storage overview](database/overview.md)
- [Memory DB overview](memory/overview.md)
- [Secret Store / platform references](platform/secrets.md)

Legacy SQLite/PostgreSQL consumers still exist during migration, so this redirect does not claim that SQL has already been removed.
