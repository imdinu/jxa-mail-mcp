# TODO - Future Improvements

## Test Suite Enhancements (Post v0.4.0)

These recommendations come from a Gemini deep-dive analysis of the test suite after the v0.4.0 refactoring that improved coverage from 40% → 52%.

### High Priority

#### Add pyfakefs tests for disk.py and sync.py
- **Why:** `disk.py` (56%) and `sync.py` (40%) handle complex file parsing and incremental sync - areas where edge cases hide
- **How:** Use `pyfakefs` pytest plugin to simulate filesystem operations
- **Benefit:** Test file walking, mail directory discovery, and sync logic without needing real macOS Mail directories

#### Refactor string assertions to use regex
- **Why:** Assertions like `assert "readStatus[i] === false" in script` are brittle - they break on whitespace changes
- **How:** Use `re.search(r'readStatus\[i\]\s*===\s*false', script)`
- **Benefit:** Tests tolerate formatting changes in generated JavaScript

### Medium Priority

#### Refactor IndexManager singleton to dependency injection
- **Why:** Manual `IndexManager._instance = None` teardown is a "code smell" - if a test crashes before teardown, it pollutes state for subsequent tests
- **How:** Pass manager instances as function parameters instead of using global singleton
- **Benefit:** Eliminates flaky tests from state pollution

#### Extract CLI logic into testable controller layer
- **Why:** `cli.py` has 13% coverage with 160 statements of untested logic
- **How:** CLI parses args → calls `controller.perform_action()` → prints output
- **Benefit:** Test controller to high coverage without invoking CLI runners

### Low Priority

#### Consider snapshot testing for JS output
- **Why:** Generated JavaScript is hard to assert on with substring matching
- **How:** Use `pytest-snapshot` to store expected JS output in separate files
- **Benefit:** Cleaner test code, easier to review changes to generated JavaScript

#### Add integration tests for watcher.py
- **Why:** `watcher.py` at 16% coverage handles real-time file watching
- **How:** Use watchdog's test utilities or run on macOS CI
- **Constraint:** Requires filesystem events, hard to unit test

---

## Current Coverage (v0.4.0)

| Module | Coverage | Status |
|--------|----------|--------|
| `builders.py` | 100% | ✅ Excellent |
| `server.py` | 97% | ✅ Excellent |
| `config.py` | 94% | ✅ Good |
| `search.py` | 73% | ✅ Good |
| `schema.py` | 69% | ✅ Acceptable |
| `manager.py` | 63% | 🟡 Needs work |
| `disk.py` | 56% | 🟡 Needs work |
| `executor.py` | 54% | 🟡 Acceptable (async paths need macOS) |
| `sync.py` | 40% | 🔴 Priority target |
| `watcher.py` | 16% | 🔴 Integration test needed |
| `cli.py` | 13% | 🔴 Extract to controller |

**Total: 152 tests, 52% coverage**

---

## Accepted Gaps

These areas are intentionally untested due to environmental constraints:

1. **JXA script execution** - Requires macOS + Mail.app, impractical for CI
2. **File watcher real-time events** - Requires filesystem events
3. **CLI integration** - Thin wrapper, low value for unit tests

These would need integration tests on a real Mac environment.
