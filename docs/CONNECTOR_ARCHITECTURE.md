# Connector architecture

Planned: explicit capabilities, connector registration, safe network policy,
bounded ordered reads, transaction-aware writes, snapshots and typed safe errors.
Initial PostgreSQL source/destination implementation will use psycopg and catalog
inspection. Supabase is a PostgreSQL destination profile, not a new datastore.

Current implementation: metadata database only. No migration connectors are
implemented or certified. Metadata connectivity is not connector acceptance.
