## Lessons
- [2026-04-14] If backend auth is enabled (`VULCAN_API_KEY` set), frontend calls to protected endpoints must include `X-API-Key`; health checks alone can still be green.

- [2026-04-14] Keep auth/offline banners in the main content area (not fixed header overlays) to avoid blocking navigation and overlapping core UI.

- [2026-04-14] Avoid ambiguous setting names (like "server default") when they control provider override behavior, not backend authentication.

- [2026-04-14] Ensure callback dependency order in React components is safe; referencing `useCallback` dependencies before initialization can blank the app at runtime.

- [2026-04-14] In Azure backend containers, install Docker CLI explicitly and verify `docker --version` inside the container before blaming workspace logic.

- [2026-04-14] Seed and permission-check `workspaces/test_repo` on Azure when default repo tasks are expected to work without user-provided `repo_url`.

- [2026-04-14] Prevent agent loops by forcing a context-shifting step after repeated failed writes on the same file path.

- [2026-04-14] Full-file rewrite guards should be size-aware: allow complete rewrites for very small files, keep stricter thresholds for larger files.

- [2026-04-14] Treat `run_tests` status `no_tests_found` as a valid completion path for simple file tasks to avoid pointless retry loops.

- [2026-04-14] Exclude runtime workspace directories from pytest collection to prevent import-mismatch noise during local backend test runs.

- [2026-04-14] Always verify the mathematical correctness of functions, especially when they are used in critical calculations.

- [2026-04-14] Carefully review the logic of financial transactions to ensure accuracy and consistency.

- [2026-04-14] Carefully review arithmetic operations and dictionary keys to prevent similar bugs in the future.

- [2026-04-14] When fixing bugs, it's essential to review the entire code path to ensure all related issues are addressed, as seen in the corrections to withdrawal, transaction count, and total withdrawn amount calculations.

- [2026-04-10] Carefully review method implementations to ensure they match the intended behavior.

- [2026-04-07] Correctly identifying and fixing off-by-one errors and incorrect keys in data structures is crucial for maintaining data integrity.

- [2026-04-07] Always verify the mathematical correctness of operations in utility functions.

- [2026-04-07] Correctly identifying and fixing off-by-one errors and incorrect keys in transaction types can significantly improve the accuracy of financial calculations.

- [2026-04-05] Always review the math logic in functions to ensure accuracy.

- [2026-04-05] Always verify the mathematical correctness of operations in utility functions.

- [2026-04-05] Always verify the mathematical correctness of operations in utility functions.

- [2026-04-05] Correctly applying arithmetic operations is crucial for financial calculations.

- [2026-04-03] When fixing bugs, focus on removing the root cause of the issue rather than just treating the symptoms.

- [YYYY-MM-DD] <add key lessons from completed reviews here>

## Context
<add durable project context here>
