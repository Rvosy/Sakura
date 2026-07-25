# WP-3-01 Shared Shutdown Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete WP-3-01 Task 5 with one 5000ms shutdown deadline, deadline-safe three-platform process-tree/pipe finalization, and an isolated RuntimeLocator-approved Assistant root.

**Architecture:** Preserve the accepted `ManagedProcessTree` and Core Host protocol boundaries, but add a consuming absolute-deadline finalizer and deadline-aware pipe readers. Separate Python `resource_root` from an explicit `assistant_root`, then make `CoreHostRuntime` use one cleanup tail that consumes the tree, finishes readers, and returns success only after resource-zero.

**Tech Stack:** Rust 1.96, Tauri 2.11.3, `windows` 0.61.3 with existing Pipes/JobObjects/Threading features, POSIX `libc` poll/process groups, Python 3.12.8 fixtures, pytest, GitHub Actions native Windows/macOS/Linux runners.

## Global Constraints

- Work only on `refactor/tauri-runtime-v2`; WP-3-01 remains the sole active WP until its accepted commit.
- The starting implementation baseline is `f5b5e49509239c920cc7dcc054c4ebfa5a6cffbd`; design commit `92f8798` is docs-only.
- From successful `system.shutdown` frame write+flush, production uses exactly 3000ms graceful inside one 5000ms total absolute deadline.
- No Drop path, guardian, reader, release helper, or error branch may create a second full timeout or detach a thread/tree owner.
- Production shutdown deadlines are Rust constants; only `#[cfg(test)]` helpers may inject proportionally shorter policies.
- Do not change Supervisor restart semantics, IPC envelopes, Snapshot schema, readiness codes, Router/Gateway/Operation, frontend, Python Adapter behavior, or Provider networking.
- Do not add or update dependencies, Cargo features, manifests, or lockfiles.
- Never delete, truncate, restore, clean, or write repository `data/`, `characters/`, or `runtime/`; fixtures live under isolated temp roots.
- Set `PYTHONDONTWRITEBYTECODE=1` for every Python and Cargo verification command.
- When a test needs `python` on PATH, create one unique `/private/tmp` shim, install a trap before running tests, and remove only that shim; never create `runtime/python.exe` or write into `runtime/`.
- Each task uses RED→GREEN, a single-purpose Chinese Conventional Commit with detailed body, a fresh independent reviewer, and Critical/Important findings fixed before the next task.
- Task 6 of the parent WP, WP-1D-01, WP-2-01, and WP-2-02 remain blocked until all tasks in this plan are complete and reviewed.

## File Responsibility Map

- `desktop/src-tauri/src/platform/contracts.rs`: object-safe deadline reader/finalizer DTOs and explicit Assistant-root layout contract.
- `desktop/src-tauri/src/platform/runtime_locator.rs`: canonical, explicit code/Assistant root resolution without fallback.
- `desktop/src-tauri/src/platform/process_tree_backend.rs`: POSIX guardian, native deadline readers, and cross-platform trait adapters.
- `desktop/src-tauri/src/managed_process_tree.rs`: accepted Windows Job implementation and deadline-aware Job accounting finalization.
- `desktop/src-tauri/src/core_host_runtime.rs`: framed transport reads, stderr drainer ownership, one shutdown budget, and cleanup aggregation.
- `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`: copied read-only ready fixture and real lifecycle evidence.
- `tests/fixtures/runtime_v2/wp_3_01/ready/**`: existing deterministic no-network Assistant fixture; it is copied, never mutated in place.

---

### Task 1: Separate Runtime code root from the explicit Assistant root

**Files:**
- Modify: `desktop/src-tauri/src/platform/contracts.rs`
- Modify: `desktop/src-tauri/src/platform/runtime_locator.rs`
- Modify: `desktop/src-tauri/src/core_host_runtime.rs`
- Modify mechanically for the new request field: `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`
- Test: inline tests in the three modules above

**Interfaces:**
- Consumes: existing `RuntimeLocationRequest`, `RuntimeLayout`, and `FilesystemRuntimeLocator`.
- Produces: required `RuntimeLocationRequest.assistant_root: PathBuf` and canonical `RuntimeLayout.assistant_root: PathBuf`; `resource_root` remains the Python code root.

- [ ] **Step 1: Write RED contract and locator tests**

Add a separated-root fixture and explicit rejection rows. The central GREEN assertions must be:

```rust
let assistant_root = fixture.path().join("assistant-root");
fs::create_dir_all(&assistant_root).unwrap();
let mut request = create_packaged_layout(&fixture, PlatformTarget::WindowsX64);
request.assistant_root = assistant_root.clone();

let layout = FilesystemRuntimeLocator.locate_fixture(&request).unwrap();
assert_eq!(layout.assistant_root, assistant_root.canonicalize().unwrap());
assert_ne!(layout.assistant_root, layout.resource_root);
assert_eq!(layout.working_directory, layout.resource_root);
```

Add table rows for `PathBuf::from("relative")` and a missing absolute directory. Both must fail with
`PlatformErrorCategory::InvalidInput` or `NotFound`, `RetryAdvice::Never`, before Core spawn.

In `core_host_runtime.rs`, replace the existing app-root command assertion with:

```rust
assert_eq!(
    request.args[app_root_index + 1].as_os_str(),
    layout.assistant_root.as_os_str()
);
assert_ne!(layout.assistant_root, layout.resource_root);
```

- [ ] **Step 2: Run RED and record the exact failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked runtime_locator::tests -- --test-threads=1
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime::tests::launch_command_uses_only -- --test-threads=1
```

Expected: compilation fails because `assistant_root` is absent, or the command still passes the code root.

- [ ] **Step 3: Implement the explicit root contract**

Change the DTOs to the following shape; remove the ambiguous compatibility layout field:

```rust
pub struct RuntimeLocationRequest {
    pub mode: RuntimeMode,
    pub target: PlatformTarget,
    pub executable_directory: PathBuf,
    pub resource_directory: PathBuf,
    pub explicit_development_root: Option<PathBuf>,
    pub assistant_root: PathBuf,
}

pub struct RuntimeLayout {
    pub target: PlatformTarget,
    pub architecture: RuntimeArchitecture,
    pub mode: RuntimeMode,
    pub runtime_root: PathBuf,
    pub python_executable: PathBuf,
    pub resource_root: PathBuf,
    pub assistant_root: PathBuf,
    pub core_entry: PathBuf,
    pub core_module: String,
    pub working_directory: PathBuf,
    pub source_id: String,
}
```

In `FilesystemRuntimeLocator::locate_internal`, resolve code and Assistant roots independently:

```rust
let resource_root = canonical_child(
    &runtime_root,
    &application_relative,
    "resolve_resource_root",
)?;
let assistant_root = canonical_existing(
    &request.assistant_root,
    "resolve_assistant_root",
)?;
if !assistant_root.is_dir() {
    return Err(locator_error(
        PlatformErrorCategory::NotFound,
        "resolve_assistant_root",
        RetryAdvice::Never,
        "Assistant root is not an existing directory",
    ));
}
```

Require `assistant_root.is_absolute()` in `validate_request_roots`. Do not require it to be inside
`runtime_root`; do not inspect configuration files in the locator.

- [ ] **Step 4: Migrate constructors and Core validation**

Use `rg -n "RuntimeLocationRequest \{" desktop/src-tauri/src` and update every constructor explicitly.
Locator unit fixtures create a dedicated temp directory. Existing development Core tests may pass `repo_root`
until Task 5 copies the ready fixture. Update `validate_runtime_layout` so code containment applies only to
`resource_root`, while `assistant_root` must be absolute, canonical, existing, and a directory. Change the sole
`--app-root` argument to `layout.assistant_root` and rename runtime-layout evidence to `assistantRoot`.

- [ ] **Step 5: Run GREEN and scope checks**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked platform::runtime_locator::tests -- --test-threads=1
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime::tests::launch_command_uses_only -- --test-threads=1
git diff --check
```

Expected: all commands exit 0; `rg -n "application_root" desktop/src-tauri/src` finds only manifest schema
names and no `RuntimeLayout.application_root` access.

- [ ] **Step 6: Commit and review Task 1**

Stage only the four Task 1 files. Commit title:

```text
refactor(runtime): 分离 Assistant 配置根
```

The body records WP, no-fallback semantics, test commands, non-goals, risk, and revert order. Dispatch a fresh
reviewer for the exact Task 1 commit range; resolve all Critical/Important findings before Task 2.

---

### Task 2: Add deadline-aware native pipe readers and remove detachable response reads

**Files:**
- Modify: `desktop/src-tauri/src/platform/contracts.rs`
- Modify: `desktop/src-tauri/src/platform/process_tree_backend.rs`
- Modify: `desktop/src-tauri/src/core_host_runtime.rs`
- Test: inline platform/Core Host tests

**Interfaces:**
- Consumes: `ManagedProcessPipes` and existing 8 MiB `FrameDecoder`.
- Produces: `ManagedPipeReader::read_until`, `ManagedPipeReadOutcome`, bounded stderr completion, and synchronous `read_response_until` without thread-per-response.

- [ ] **Step 1: Write RED pipe and reader-ownership tests**

Freeze the object-safe contract with these exact outcome variants:

```rust
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ManagedPipeReadOutcome {
    Read(usize),
    Eof,
    Cancelled,
    TimedOut,
}

pub trait ManagedPipeReader: Send {
    fn read_until(
        &mut self,
        buffer: &mut [u8],
        deadline: Instant,
        cancelled: &AtomicBool,
    ) -> PlatformResult<ManagedPipeReadOutcome>;
}
```

Add native tests that spawn a child which holds stdout open without writing. A 50ms deadline must return
`TimedOut` in less than 500ms; setting cancellation before the call must return `Cancelled`; normal output must
produce `Read` followed by `Eof`. In Core tests add an injected timeout row that asserts the response read does
not increase a test-only active-reader counter after returning.

- [ ] **Step 2: Run RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked process_tree_backend::tests -- --test-threads=1
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime::tests -- --test-threads=1
```

Expected: contract symbols are absent and the existing stdout reader can still detach on its second timeout.

- [ ] **Step 3: Implement native reader wrappers**

Change `ManagedProcessPipes.stdout/stderr` to `Box<dyn ManagedPipeReader>`, retaining stdin as `File`.
Implement one platform wrapper in `process_tree_backend.rs`:

```rust
const PIPE_POLL_QUANTUM: Duration = Duration::from_millis(10);

struct NativePipeReader {
    file: File,
}
```

On POSIX, poll `POLLIN|POLLHUP|POLLERR` for
`min(deadline.saturating_duration_since(now), PIPE_POLL_QUANTUM)`, then perform the single-owner read. On
Windows, call `PeekNamedPipe`; read no more than the reported available byte count and sleep for at most the
same quantum when zero bytes are available. Map broken-pipe/closed-handle to `Eof`, deadline to `TimedOut`, and
the atomic flag to `Cancelled`. Preserve native error codes in `PlatformError`; do not log paths or data.

- [ ] **Step 4: Replace stdout thread-per-response**

Use `FrameDecoder` on the calling thread:

```rust
fn read_frame_until(
    reader: &mut dyn ManagedPipeReader,
    deadline: Instant,
    cancelled: &AtomicBool,
) -> Result<Option<Value>, String> {
    let mut decoder = FrameDecoder::default();
    let mut chunk = [0_u8; 8192];
    loop {
        match reader.read_until(&mut chunk, deadline, cancelled).map_err(|e| e.to_string())? {
            ManagedPipeReadOutcome::Read(count) => {
                let mut frames = decoder.feed(&chunk[..count]).map_err(|e| e.to_string())?;
                if let Some(frame) = frames.pop() {
                    if !frames.is_empty() {
                        return Err("TRANSPORT_READ_FAILED: unexpected response burst".to_string());
                    }
                    return Ok(Some(frame));
                }
            }
            ManagedPipeReadOutcome::Eof => {
                decoder.finish().map_err(|e| e.to_string())?;
                return Ok(None);
            }
            ManagedPipeReadOutcome::Cancelled => {
                return Err("TRANSPORT_READ_CANCELLED: stdout read was cancelled".to_string())
            }
            ManagedPipeReadOutcome::TimedOut => {
                return Err("REQUEST_DEADLINE_EXCEEDED: Core Host response exceeded its deadline".to_string())
            }
        }
    }
}
```

Use the existing `FrameDecoder::finish()` EOF validation exactly as shown; do not modify
`core_host_protocol.rs` or framing semantics in this task.

- [ ] **Step 5: Make stderr completion explicit and bounded**

Move the boxed reader into the single drainer thread with an `Arc<AtomicBool>` cancellation flag and a
`sync_channel(1)` completion notification. The loop treats a poll timeout as “continue”, EOF as final flush, and
cancel as final flush without setting `read_failed`. `finish_until(deadline)` waits on completion using only
remaining time, then joins after completion is observed. Remove the joining `Drop`; Drop sets cancellation but
normally observes `reader=None`. Its insurance branch sets cancellation and joins the cooperative reader, whose
contract returns within one 10ms poll quantum; it does not start a new timeout or claim successful cleanup.

- [ ] **Step 6: Run GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked platform::process_tree_backend::tests -- --test-threads=1
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime::tests -- --test-threads=1
git diff --check
```

Expected: all commands exit 0; `rg -n "thread::spawn\(move \|\|.*read_frame|reader\.join\(\)" desktop/src-tauri/src/core_host_runtime.rs`
finds no stdout response thread or unconditional reader join.

- [ ] **Step 7: Commit and review Task 2**

Commit title:

```text
refactor(runtime): 建立可截止的 Core 管道读取
```

Stage only the three Task 2 files. Review reader ownership, Windows/POSIX EOF mapping, no busy-spin, no detached
handle, framing equivalence, and secret safety.

---

### Task 3: Add one consuming process-tree finalizer on Windows and POSIX

**Files:**
- Modify: `desktop/src-tauri/src/platform/contracts.rs`
- Modify: `desktop/src-tauri/src/platform/process_tree_backend.rs`
- Modify: `desktop/src-tauri/src/managed_process_tree.rs`
- Test: inline native process-tree tests

**Interfaces:**
- Consumes: existing wait/terminate/verify/release APIs and an absolute `Instant`.
- Produces: `ManagedProcessTree::finalize_until(self: Box<Self>, deadline: Instant, reason_code: u32)` returning `ProcessTreeFinalization { root_status, forced }` only after tree/resource zero.

- [ ] **Step 1: Write RED finalizer tests**

Add the DTO and trait call to tests before implementation:

```rust
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProcessTreeFinalization {
    pub root_status: ProcessExitStatus,
    pub forced: bool,
}
```

Platform tests must cover normal exit, a holding root, root-first exit with one TERM-ignoring descendant, two
descendant levels, repeated pre-finalize observations, and an already-expired deadline. For forced rows:

```rust
let started = Instant::now();
let result = tree
    .finalize_until(started + Duration::from_secs(2), 97)
    .expect("managed tree must finalize inside the one deadline");
assert!(result.forced);
assert!(started.elapsed() < Duration::from_secs(2));
```

On Windows, sample process/Job handle count before and after a bounded loop. On POSIX, assert guardian PID and
PGID no longer exist and the control/status fd count returns to baseline.

- [ ] **Step 2: Run RED on the current native platform**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked process_tree_backend::tests -- --nocapture --test-threads=1
```

Expected: compilation fails because `finalize_until` and `ProcessTreeFinalization` are absent.

- [ ] **Step 3: Implement Windows Job finalization**

In `managed_process_tree.rs`, add an internal method with this order:

```text
zero-time root observation
-> zero-time Job accounting observation
-> if any process remains: TerminateJobObject(reason), forced=true
-> WaitForSingleObject(root, remaining)
-> poll Job accounting with remaining absolute budget
-> read/cache root exit code
-> release process then Job handles
-> return finalization
```

Every call recomputes remaining time from the same `Instant`. An expired deadline still performs immediate
terminate/kill-on-close and handle close, but returns `TimedOut`; it never starts a 5000ms rollback. Keep the
accepted spawn rollback behavior unchanged because it occurs before a Core shutdown intent.

- [ ] **Step 4: Implement POSIX explicit finalization**

At the first line, store `cleanup_deadline: Some(deadline)` in `TreeState`. If root or group remains, close the
control writer, send TERM directly to the verified PGID, allow at most
`min(TERMINATE_GRACE, remaining)`, then send KILL. Pump guardian status only with remaining time, require
`TREE_EXITED`, and reap guardian by `try_wait` polling; do not call unbounded `wait()`.

Change guardian cleanup so explicit-control shutdown never receives its own `FORCE_WAIT`. The parent-death EOF
insurance may retain a crash-only ceiling only when no explicit deadline was armed. Change `NativeTree::Drop`
to immediate close/signal/kill/handle release with no `pump_until`, sleep, or `guardian.wait()`.

- [ ] **Step 5: Wire the common trait without migrating legacy consumers**

Add `finalize_until` to `ManagedProcessTree` and implement it in both cfg wrappers. Keep wait/terminate/verify/
release methods for WP-1P-04 consumers. Map platform errors to stable categories and operation names; do not
include program paths, PGIDs, credentials, stderr, or command arguments in public error text.

- [ ] **Step 6: Run GREEN and static hidden-wait audit**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked platform::process_tree_backend::tests -- --nocapture --test-threads=1
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked managed_process_tree -- --nocapture --test-threads=1
rg -n "FORCE_WAIT|guardian\.wait\(\)|pump_until\(&mut state, FORCE_WAIT" desktop/src-tauri/src/platform/process_tree_backend.rs
git diff --check
```

Expected: tests pass. Any remaining `FORCE_WAIT` is confined to documented unarmed parent-death insurance;
`release_exited` and armed Drop contain no unbounded wait.

- [ ] **Step 7: Commit and review Task 3**

Commit title:

```text
refactor(runtime): 统一三平台进程树终结期限
```

Review Windows and POSIX independently. Reject success without Job/group zero evidence, a Drop second budget,
PID/PGID reconstruction, changed spawn containment, or a new dependency.

---

### Task 4: Make CoreHostRuntime use the single shutdown budget and one cleanup tail

**Files:**
- Modify: `desktop/src-tauri/src/core_host_runtime.rs`
- Add: `tests/fixtures/runtime_v2/wp_3_01/slow_shutdown_host.py`
- Use: `tests/fixtures/runtime_v2/wp_1c_01/ignoring_shutdown_host.py`
- Test: inline Core Host runtime tests

**Interfaces:**
- Consumes: `ManagedPipeReader`, `ManagedProcessTree::finalize_until`, explicit `assistant_root`.
- Produces: production `CoreHostRuntime::shutdown(self)` with fixed `ShutdownPolicy { graceful: 3000ms, total: 5000ms }`, no segmented timeout, and a fully consumed successful exit.

- [ ] **Step 1: Write RED policy, timing, and cleanup-order tests**

Freeze production values and a test-only constructor:

```rust
#[derive(Clone, Copy)]
struct ShutdownPolicy {
    graceful: Duration,
    total: Duration,
}

const PRODUCTION_SHUTDOWN_POLICY: ShutdownPolicy = ShutdownPolicy {
    graceful: Duration::from_millis(3000),
    total: Duration::from_millis(5000),
};
```

Add tests for: cooperative exit; slow host consuming 2900-3000ms before exit; ignore shutdown; root-first
descendant; trailing stdout; stderr flood; reader timeout; tree finalizer error; stderr completion error. The
slow and ignore rows measure from the test-visible successful shutdown write marker and assert total elapsed is
below 5500ms on local/CI while the injected fake-clock test proves exact 5000ms budget identity.

Add a fake tree/reader event log and require this order:

```rust
assert_eq!(events, [
    "shutdown_written",
    "stdin_closed",
    "tree_finalized",
    "stdout_drained",
    "stderr_finished",
    "readers_dropped",
]);
assert_eq!(deadlines.iter().copied().collect::<BTreeSet<_>>().len(), 1);
```

- [ ] **Step 2: Run RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime::tests -- --nocapture --test-threads=1
```

Expected: `shutdown` still accepts two relative durations; timing rows expose 3000+5000 behavior or unbounded
drain/join; cleanup-order fakes do not observe a consuming finalizer.

- [ ] **Step 3: Split write completion from response validation**

Introduce an internal `RequestExpectation` holding id/name/minor and a `write_request_frame` helper that returns
the `Instant` sampled immediately after flush succeeds. Regular requests call `read_response_until(written_at +
deadline)`. Shutdown calls it with protocol `deadlineMs=3000`, then derives both deadlines from the returned
`written_at`; it does not call the old `request(..., Duration)` wrapper.

- [ ] **Step 4: Implement one cleanup tail**

Store tree/stdout/stderr as `Option` so cleanup can consume each owner exactly once. Replace `finish_exit` with
an internal function equivalent to:

```rust
fn finish_exit_until(
    mut self,
    absolute_deadline: Instant,
    primary: Option<String>,
) -> Result<CoreHostExit, String> {
    self.stdin.take();
    let tree_result = self.tree.take().expect("tree owner").finalize_until(
        absolute_deadline,
        DEADLINE_EXIT_CODE,
    );
    let stdout_result = drain_trailing_stdout_until(
        self.stdout.as_deref_mut().expect("stdout owner"),
        absolute_deadline,
    );
    let stderr_result = self.stderr_drain.as_mut().expect("stderr owner").finish_until(
        absolute_deadline,
    );
    self.stdout.take();
    self.stderr_drain.take();
    aggregate_exit(primary, tree_result, stdout_result, stderr_result)
}
```

The real implementation must not use `expect` on recoverable state; missing owners become stable cleanup
failures while later cleanup continues. `aggregate_exit` preserves the first protocol/transport error and adds
only stable operation/type notes. Success requires a finalization result, stdout EOF with no pollution, stderr
completion, and all owners consumed before `absolute_deadline`.

- [ ] **Step 5: Cover non-shutdown failure cleanup**

Credential bootstrap failure, stale credential, request timeout, and explicit stdin EOF each create one recovery
deadline and call the same consuming cleanup tail. Remove every fixed forced 5s wait, unbounded `read_to_end`,
unconditional `JoinHandle::join`, and direct `release_exited` call from `CoreHostRuntime`.

- [ ] **Step 6: Run GREEN and exact timeout audit**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime::tests -- --nocapture --test-threads=1
rg -n "Duration::from_secs\(5\)|read_to_end|recv_timeout\(Duration::from_secs|release_exited\(\)|reader\.join\(\)" desktop/src-tauri/src/core_host_runtime.rs
git diff --check
```

Expected: tests pass; each remaining 5s literal is unrelated to shutdown or is the single production policy;
no unbounded drain, second recv timeout, direct release, or unconditional reader join remains.

- [ ] **Step 7: Commit and review Task 4**

Commit title:

```text
fix(runtime): 统一 Assistant 关闭期限与资源回收
```

Review the exact t0 sampling point, remaining-budget propagation, error cleanup continuation, reader/tree owner
consumption, timing-test tolerance versus exact fake-clock proof, and unchanged readiness/retry behavior.

---

### Task 5: Reconnect deterministic real-ready acceptance and close Task 5 gates

**Files:**
- Modify: `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`
- Modify only for integrated assertions: `desktop/src-tauri/src/core_host_runtime.rs`
- Use unchanged: `tests/fixtures/runtime_v2/wp_3_01/ready/**`
- Update untracked execution evidence: `.superpowers/sdd/task-5-report.md`, `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: Tasks 1-4 production path and the existing read-only ready fixture.
- Produces: Phase 1C `ready` through the real Adapter with copied isolated config/characters, precise shutdown evidence, and a reviewed complete Task 5.

- [ ] **Step 1: Write RED fixture-copy and real-ready tests**

Add unit tests for a recursive copy helper that accepts only regular files/directories, rejects symlinks, and
records relative path/length/mtime/SHA-256 before and after. In `run_scenario`, assert the final Snapshot is:

```rust
assert_eq!(snapshot["readiness"], "ready");
assert_eq!(snapshot["components"]["assistant"]["state"], "ready");
assert_eq!(snapshot["components"]["assistant"]["code"], "READY");
assert_eq!(snapshot["components"]["assistant"]["retryable"], false);
let summary_keys = snapshot["currentCharacterSummary"]
    .as_object()
    .unwrap()
    .keys()
    .map(String::as_str)
    .collect::<BTreeSet<_>>();
assert_eq!(
    summary_keys,
    BTreeSet::from(["displayName", "id", "initialMessage", "portraitChoices", "replyTones"]),
);
```

The test must fail if `layout.assistant_root` is the repo root or if the copied fixture changes after Core exit.
Keep the historical `hang` mode explicitly unsupported in this Task 5 acceptance path; Task 6 will replace it
with the full close-block/fault matrix instead of a first-Snapshot race.

- [ ] **Step 2: Run RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked phase_1c_core_host_acceptance::tests -- --test-threads=1
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime::tests::staged_packaged_runtime_runs_lifecycle_faults_and_clean_generations -- --ignored --nocapture --test-threads=1
```

The ignored packaged test requires `SAKURA_WP_1C_04_PACKAGED_RESOURCES`; if it is not staged locally, record
the explicit missing-fixture precondition and run it in the native platform workflow. The Phase 1C unit test must
fail before implementation because no copied Assistant root exists.

- [ ] **Step 3: Copy and approve the isolated fixture**

Create `assistant-root` under the already validated acceptance directory. Source is the canonical repository
`tests/fixtures/runtime_v2/wp_3_01/ready`. Walk with `symlink_metadata`; reject every symlink, device, socket,
FIFO, or path escape; create directories and copy regular files only. Canonicalize the result and pass it as
`RuntimeLocationRequest.assistant_root`. Never alter or delete the source fixture.

Remove the `ready|hang` race from `run_scenario`: this Task 5 path accepts only `ready`, waits until readiness is
not `initializing`, then requires the exact real Adapter Snapshot above. Preserve repeated health and Shell close.

- [ ] **Step 4: Record shared-deadline/resource-zero evidence**

Immediately before `host.shutdown()`, record a monotonic start; after return require elapsed below 5000ms plus
the frozen platform-test scheduling tolerance, `tree_empty=true`, no forced termination for the ready row,
root exit code 0, empty sanitized stderr, reader completion true, and no Core/descendant identity. Write only
non-sensitive booleans/counts/durations to acceptance evidence. Verify copied fixture manifest unchanged before
writing `acceptance.cleaned`.

- [ ] **Step 5: Run complete local Task 5 gates**

Use one unique shim only if the full Rust suite needs `python`:

```bash
set -euo pipefail
shim_dir="$(mktemp -d /private/tmp/sakura-task5-python.XXXXXX)"
trap 'rm -f -- "$shim_dir/python"; rmdir -- "$shim_dir"' EXIT
ln -s /Users/beyondpower/Music/Projects/Sakura/runtime/bin/python3.12 "$shim_dir/python"
export PATH="$shim_dir:$PATH"
export PYTHONDONTWRITEBYTECODE=1
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked -- --test-threads=1
cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked
cargo build --manifest-path desktop/src-tauri/Cargo.toml --release --locked
git diff --check
```

Also run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 -m pytest tests/unit/test_core_host_*.py tests/integration/test_core_host_*.py -q
npm test --prefix desktop/frontend
```

After the commands, prove no shim, Shell/Core/descendant, shared-lock holder, reader thread, guardian, or
acceptance temp remains. Record read-only protected-directory summaries without removing new logs/cache.

- [ ] **Step 6: Commit and perform complete Task 5 review**

Commit title:

```text
test(runtime): 接入隔离 Assistant readiness 验收
```

The body records exact Task 1-4 SHAs and the Task 5 base, RED/GREEN commands, no-network/no-write evidence,
protected-directory delta, non-goals, risks, and independent revert. After commit, record the resulting Task 5
SHA in `.superpowers/sdd/task-5-report.md`. Generate a review package from the pre-plan implementation SHA
through this commit; dispatch a fresh broad reviewer. Fix every Critical/Important finding in one focused fix
wave and re-review before marking `.superpowers/sdd/progress.md` Task 5 complete.

- [ ] **Step 7: Push and close the current CI regression before Task 6**

Push `refactor/tauri-runtime-v2` normally. For the exact pushed SHA, monitor Unit/UI and Runtime v2 platform
foundation push/PR runs. Use `superpowers:systematic-debugging` and `github:gh-fix-ci` for failures. Require
Windows x64, macOS arm64, and Linux x64 Core lifecycle jobs to reach real `ready`; do not accept
setup_required/failed, rerun without a root-cause classification, or change protected data. Only after exact-SHA
checks are green may the parent WP proceed to Task 6.

## Plan Completion Criteria

- Five implementation tasks have individual RED/GREEN evidence, commits, and clean independent reviews.
- `CoreHostRuntime::shutdown()` uses one internal 3000ms/5000ms policy sampled after successful frame flush.
- Windows Job and POSIX guardian/group are verified empty and released within the same absolute deadline.
- stdout/stderr have no detached reader and complete before success.
- RuntimeLocator supplies a distinct explicit `assistant_root`; no cwd/repo/home/env fallback exists.
- Phase 1C real-ready uses the copied `wp_3_01/ready` fixture and leaves source/protected roots unchanged.
- Full local gates and exact-SHA three-platform CI are green.
- WP-3-01 remains active and Task 6 has not started until all criteria above are proven.
