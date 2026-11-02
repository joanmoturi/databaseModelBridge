# Connector architecture

Planned: explicit capabilities, connector registration, safe network policy,
bounded ordered reads, transaction-aware writes, snapshots and typed safe errors.
Initial PostgreSQL source/destination implementation will use psycopg and catalog
inspection. Supabase is a PostgreSQL destination profile, not a new datastore.

Current implementation: PostgreSQL source/destination through SQLAlchemy/psycopg,
with actual transfers, snapshots, deterministic crosswalks and reconciliation.
PostgreSQL 17.11 passed native integration; classification is Experimental, not
production-supported, while clean deployment/security acceptance is incomplete.
Other versions/products and hosted Supabase have not been tested.
See MIGRATION_EXECUTION.md for supported schema/types and explicit limitations.
