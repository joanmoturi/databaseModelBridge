# Testing strategy

Run locked Python tests and static checks, frontend tests/lint/type/build, then
real PostgreSQL migrations and API/service tests using a disposable database.
Integration tests require MODELBRIDGE_TEST_DATABASE_URL and the explicit
MODELBRIDGE_TEST_ALLOW_DATABASE_WRITES=true acknowledgement. Never point these at
an existing or production database. Test objects use generated schemas, not drops
of arbitrary existing tables.

Native TCP smoke should start/stop its own bounded API/worker/dashboard processes
and independently verify persisted metadata after restart. It is not a genuinely
VM-isolated Compose acceptance run. Keep that distinction in reports.

Subsequent milestones require real synthetic student migration and independent
destination SQL, security-negative scenarios, retries/checkpoints/cancellation,
browser E2E and connector-specific test evidence. No skipped result counts as pass.
