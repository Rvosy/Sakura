---
kind: plan
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# WP-3-01 Shared Shutdown Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete WP-3-01 Task 5 with one 5000ms shutdown deadline, deadline-safe three-platform process-tree/pipe finalization, and an isolated RuntimeLocator-approved Assistant root.

**Architecture:** Preserve the accepted `ManagedProcessTree` and Core Host protocol boundaries, but add a success-consuming absolute-deadline finalizer that returns the same recovery owner on failure, plus deadline-aware pipe readers. Separate Python `resource_root` from an explicit `assistant_root`, then make `CoreHostRuntime` use one cleanup tail that consumes the tree only on resource-zero success and otherwise returns a typed recovery capsule.

**Tech Stack:** Rust 1.96, Tauri 2.11.3, `windows` 0.61.3 with existing Pipes/JobObjects/Threading features, POSIX `libc` poll/process groups, Python 3.12.8 fixtures, pytest, GitHub Actions native Windows/macOS/Linux runners.

## Global Constraints

- Work only on `refactor/tauri-runtime-v2`; WP-3-01 remains the sole active WP until its accepted commit.
- The starting implementation baseline is `f5b5e49509239c920cc7dcc054c4ebfa5a6cffbd`; design commit `92f8798` is docs-only.
- From successful `system.shutdown` frame write+flush, production uses exactly 3000ms graceful inside one 5000ms total absolute deadline.
- No Drop path, guardian, reader, release helper, or error branch may create a second full timeout or detach a thread/tree owner. A failed finalizer returns the original recovery owner without automatically retrying.
- `finalize_until` success consumes and releases the tree; failure returns `ProcessTreeFinalizationFailure { error, recovery }`. Recovery success is a separate explicitly invoked operation, and no generation may become stopped or be replaced while the capsule remains unresolved.
- Production shutdown deadlines are Rust constants; only `#[cfg(test)]` helpers may inject proportionally shorter policies.
- Do not change Supervisor restart semantics, IPC envelopes, Snapshot schema, readiness codes, Router/Gateway/Operation, frontend, Python Adapter behavior, or Provider networking.
- Do not add or update dependencies, Cargo features, manifests, or lockfiles.
- Repository `data/` is writable runtime state: allow task-scoped product writes and audit declared expected/forbidden write sets. Never delete, truncate, restore, or clean unrelated user data; use isolated temp roots for destructive fault injection, and modify `characters/` or `runtime/` only when explicitly in scope.
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
- `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`: copied deterministic ready fixture and real lifecycle evidence.
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

### Task 3: Complete the success-consuming process-tree finalizer with recovery ownership

**Files:**
- Modify: `desktop/src-tauri/src/platform/contracts.rs`
- Modify: `desktop/src-tauri/src/platform/process_tree_backend.rs`
- Modify: `desktop/src-tauri/src/managed_process_tree.rs`
- Modify mechanically for explicit test-double conformance: `desktop/src-tauri/src/core_host_runtime.rs`
- Test: inline native process-tree and injected-tree tests

**Interfaces:**
- Consumes: the strict native finalizer and absolute-deadline fixes through `e2be6a2`, plus the approved scheme C design in `675a494`.
- Produces: `ManagedProcessTree::finalize_until(self: Box<Self>, deadline: Instant, reason_code: u32) -> ProcessTreeFinalizationResult`, where success consumes/releases every native owner and failure returns `ProcessTreeFinalizationFailure { error, recovery }` containing the same owner.

- [ ] **Step 1: Write RED recovery-owner contract and native tests**

Add the result types and expired-owner behavior to tests before changing production signatures:

```rust
pub type ProcessTreeFinalizationResult =
    Result<ProcessTreeFinalization, ProcessTreeFinalizationFailure>;

let identity = native_tree_identity(&*tree);
let failure = tree
    .finalize_until(Instant::now(), 97)
    .expect_err("expired finalization must return the recovery owner");
assert_eq!(failure.error().category, PlatformErrorCategory::TimedOut);
let (error, recovery) = failure.into_parts();
assert_eq!(error.category, PlatformErrorCategory::TimedOut);
assert_eq!(recovery.native_owner_pid_for_test(), Some(identity.guardian_pid));

let result = recovery
    .finalize_until(Instant::now() + Duration::from_secs(2), 97)
    .expect("explicit recovery must reap the same native owner");
assert!(result.forced);
assert_posix_identity_gone(identity);
```

The POSIX row applies the existing `.superpowers/sdd/task-5c-i1-resource-zero-red.diff` condition but changes
the first-call expectation from impossible synchronous reap to `TimedOut + same owner`; the second explicit
call must reap guardian/PGID/fds to zero. The Windows cfg row must prove the first expired call retains process
and Job handles and the second call reaches Job accounting zero before releasing them. Add a counter fake that
proves the first failure invokes `finalize_until` exactly once and does not start recovery automatically.

- [ ] **Step 2: Run RED on native and test-double paths**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked platform::process_tree_backend::tests::expired_finalizer -- --nocapture --test-threads=1
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime::tests --no-run
```

Expected: the native test cannot recover an owner because the current API returns only `PlatformError`; the
Core test build exposes every test double relying on the old production default implementation.

- [ ] **Step 3: Freeze the explicit failure type and remove the trait default**

In `platform/contracts.rs`, define the non-clone, non-serializable failure type with exactly these operations:

```rust
pub struct ProcessTreeFinalizationFailure {
    error: PlatformError,
    recovery: Box<dyn ManagedProcessTree>,
}

impl ProcessTreeFinalizationFailure {
    pub fn new(error: PlatformError, recovery: Box<dyn ManagedProcessTree>) -> Self;
    pub fn error(&self) -> &PlatformError;
    pub fn into_parts(self) -> (PlatformError, Box<dyn ManagedProcessTree>);
}
```

Implement a redacted `Debug` that prints the error and `has_recovery_owner=true` only. Remove the production
default body from `ManagedProcessTree::finalize_until`; every backend/test double must implement the method.

- [ ] **Step 4: Preserve the POSIX owner on every finalization error**

Make the POSIX trait wrapper own `self` across the platform attempt. On success, set `released=true` only after
`TREE_EXITED`, PGID zero, guardian `try_wait` reap and root status are proven. On every error/timeout, immediately
close the control writer and signal the frozen PGID/guardian as already required, but do not set `released=true`
and do not drop `Child`/status ownership; return `ProcessTreeFinalizationFailure::new(error, self)`. A later
explicit call on `recovery` uses its new caller deadline to reap the same guardian. Drop remains immediate
kill/close insurance and never waits or claims success.

- [ ] **Step 5: Preserve Windows process/Job handles on error and release only on success**

Change the Windows internal finalizer to operate on `&mut self`: it may terminate the Job and observe root/Job
within the caller deadline, but any error leaves `process` and `job` owned. Only a successful root observation,
Job accounting zero and exit-code read may `take()` process then Job handles. The platform wrapper maps an
internal error to `ProcessTreeFinalizationFailure::new(stable_error, self)`. Keep suspended spawn,
assignment-before-resume and pre-shutdown rollback unchanged.

- [ ] **Step 6: Make all test doubles explicit without changing production Core behavior**

In `core_host_runtime.rs`, update `InjectedTree` and other `ManagedProcessTree` test implementations to declare
`finalize_until` explicitly. Failure fakes return themselves in `ProcessTreeFinalizationFailure`; success fakes
return `ProcessTreeFinalization`. Do not change `CoreHostRuntime::shutdown` or its production cleanup path in
this task; Task 4 consumes the new result.

- [ ] **Step 7: Run GREEN, cross-target type checks and ownership audits**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked platform::process_tree_backend::tests -- --nocapture --test-threads=1
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime::tests --no-run
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked managed_process_tree -- --nocapture --test-threads=1
rg -n "fn finalize_until|ProcessTreeFinalizationFailure|released = true|process\.take\(\)|job\.take\(\)" desktop/src-tauri/src/platform/contracts.rs desktop/src-tauri/src/platform/process_tree_backend.rs desktop/src-tauri/src/managed_process_tree.rs desktop/src-tauri/src/core_host_runtime.rs
rg -n "FORCE_WAIT|guardian\.wait\(\)|pump_until\(&mut state, FORCE_WAIT" desktop/src-tauri/src/platform/process_tree_backend.rs
git diff --check
```

Also repeat the existing temporary non-repository `llvm-rc` MSVC target check. Expected: native tests pass;
Windows cfg/tests type-check; every production/test backend implements the trait; success paths release owners;
error paths return them; remaining `FORCE_WAIT` is confined to unarmed parent-death insurance.

- [ ] **Step 8: Commit and independently re-review Task 3**

Commit title:

```text
fix(runtime): 保留进程终结恢复所有权
```

Stage only the four allowed files. The review covers the complete `0f487d4..HEAD` Task 3 range and must close
I1 while preserving the already-closed I2/I3 findings. Reject owner reconstruction, automatic recovery, a
success result before Job/group/guardian zero, secret/native identity exposure, or any new full timeout.

---

### Task 4: Make CoreHostRuntime use the single shutdown budget and one cleanup tail

**Files:**
- Modify: `desktop/src-tauri/src/core_host_runtime.rs`
- Modify mechanically for typed launch/shutdown failures: `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`
- Add: `tests/fixtures/runtime_v2/wp_3_01/slow_shutdown_host.py`
- Use: `tests/fixtures/runtime_v2/wp_1c_01/ignoring_shutdown_host.py`
- Test: inline Core Host runtime tests

**Interfaces:**
- Consumes: `ManagedPipeReader`, scheme C `ProcessTreeFinalizationResult`, explicit `assistant_root`.
- Produces: production `CoreHostRuntime::launch(...)` and `shutdown(self)` with a unified typed `CoreHostLifecycleFailure`; shutdown uses fixed `ShutdownPolicy { graceful: 3000ms, total: 5000ms }`, no segmented timeout, and returns either a fully consumed successful exit or a failure retaining the tree recovery capsule.

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

Freeze the typed failure boundary before implementation:

```rust
pub struct CoreHostLifecycleFailure {
    diagnostic: String,
    recovery: Option<CoreHostRecovery>,
}

struct CoreHostRecovery {
    tree: Box<dyn ManagedProcessTree>,
}
```

Only expose a redacted diagnostic accessor and consuming `into_recovery`; never implement Clone/Serialize or
format the native owner. A fake finalizer error must return a capsule, record exactly one shutdown deadline,
and prove no automatic second `finalize_until` call occurs. A test-only explicit recovery call supplies a new
deadline, succeeds, and only then permits the stopped/resource-zero assertion.

Also expose consuming `into_terminal_diagnostic` only for a caller that has already committed to process exit
with no next generation. Do not implement `From<CoreHostLifecycleFailure> for String` or another implicit owner-
dropping conversion. Update Phase 1C error branches explicitly: its worker immediately calls `app.exit(3)`, so
it may use terminal conversion; its normal launch/shutdown path must never receive a recovery capsule.

`CoreHostRuntime::launch` and `launch_with_backend` return the same failure type. Validation/spawn errors before
an owner exists use `recovery=None`; credential bootstrap or initialization failures after spawn must build the
same cleanup tail and retain `recovery=Some(...)` if finalization cannot prove zero. No spawn-owned error may be
collapsed to `String` before the recovery field is inspected.

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
) -> Result<CoreHostExit, CoreHostLifecycleFailure> {
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
    aggregate_exit_or_retain_recovery(primary, tree_result, stdout_result, stderr_result)
}
```

The real implementation must not use `expect` on recoverable state; missing owners become stable cleanup
failures while later cleanup continues. Aggregation preserves the first protocol/transport error and adds only
stable operation/type notes. If tree finalization failed, later pipe/thread cleanup still runs, but the returned
`CoreHostLifecycleFailure` retains the recovery owner and does not claim stopped. Success requires a finalization
result, stdout EOF with no pollution, stderr completion, and all owners consumed before `absolute_deadline`.

- [ ] **Step 5: Cover non-shutdown failure cleanup and explicit recovery ownership**

Credential bootstrap failure, stale credential, request timeout, and explicit stdin EOF each create one recovery
deadline and call the same consuming cleanup tail. Remove every fixed forced 5s wait, unbounded `read_to_end`,
unconditional `JoinHandle::join`, and direct `release_exited` call from `CoreHostRuntime`. Tree failure must not
be converted to `String` or dropped. The current operation returns the capsule without retrying; a separately
invoked recovery operation may use a new deadline, and no new generation/stopped result is allowed before it
succeeds.

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
consumption, typed Phase 1C callsites, timing-test tolerance versus exact fake-clock proof, and unchanged
readiness/retry behavior.

---

### Task 5: Reconnect deterministic real-ready acceptance and close Task 5 gates

**Files:**
- Modify: `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`
- Modify only for integrated assertions: `desktop/src-tauri/src/core_host_runtime.rs`
- Use unchanged: `tests/fixtures/runtime_v2/wp_3_01/ready/**`
- Update untracked execution evidence: `.superpowers/sdd/task-5-report.md`, `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: Tasks 1-4 production path and the existing deterministic ready fixture.
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

The ready row must return no recovery capsule. Fault rows that intentionally exhaust the first deadline record
only that first elapsed interval, assert `failed/stopping + same recovery owner`, then invoke a separately timed
test recovery operation and require resource-zero before the acceptance directory is released. Never add the
recovery duration to a passing shutdown measurement or start the next generation before recovery succeeds.

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
acceptance temp remains. Record the scenario's expected/forbidden write delta; do not require whole-directory
zero-change summaries or remove newly produced logs/cache merely to satisfy a gate.

- [ ] **Step 6: Commit and perform complete Task 5 review**

Commit title:

```text
test(runtime): 接入隔离 Assistant readiness 验收
```

The body records exact Task 1-4 SHAs and the Task 5 base, RED/GREEN commands, no-network evidence,
the scoped expected/forbidden write delta, non-goals, risks, and independent revert. After commit, record the resulting Task 5
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
- A failed first finalization returns the original native recovery owner without automatic retry; an explicit
  later recovery reaches resource-zero, and no stopped/new-generation transition occurs between the two calls.
- stdout/stderr have no detached reader and complete before success.
- RuntimeLocator supplies a distinct explicit `assistant_root`; no cwd/repo/home/env fallback exists.
- Phase 1C real-ready uses the copied `wp_3_01/ready` fixture and leaves source/protected roots unchanged.
- Full local gates and exact-SHA three-platform CI are green.
- WP-3-01 remains active and Task 6 has not started until all criteria above are proven.
