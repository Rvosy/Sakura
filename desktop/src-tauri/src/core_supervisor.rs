use std::{
    fmt,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct GenerationId(u128);

impl GenerationId {
    pub fn as_u128(self) -> u128 {
        self.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SupervisorState {
    Stopped,
    Spawning,
    Running,
    Stopping,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LifecycleIntent {
    Start,
    Stop,
    Restart,
    Retry,
    AppShutdown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StopReason {
    User,
    Restart,
    Failure,
    AppShutdown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FailureReason {
    UnexpectedExit,
    TemporarySpawnFailure,
    HelloTimeout,
    InitializeTimeout,
    ConnectionLost,
    ProtocolMajorIncompatible,
    MissingRequiredCapability,
    SetupRequired,
    DeterministicConfiguration,
    DeterministicRuntime,
    SecurityBoundary,
}

#[derive(Clone)]
pub struct GenerationCancellation {
    generation_id: GenerationId,
    cancelled: Arc<AtomicBool>,
}

impl GenerationCancellation {
    fn new(generation_id: GenerationId) -> Self {
        Self {
            generation_id,
            cancelled: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn generation_id(&self) -> GenerationId {
        self.generation_id
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    fn cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
    }
}

impl fmt::Debug for GenerationCancellation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("GenerationCancellation")
            .field("generation_id", &self.generation_id)
            .field("cancelled", &self.is_cancelled())
            .finish()
    }
}

impl PartialEq for GenerationCancellation {
    fn eq(&self, other: &Self) -> bool {
        self.generation_id == other.generation_id && Arc::ptr_eq(&self.cancelled, &other.cancelled)
    }
}

impl Eq for GenerationCancellation {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LifecycleAction {
    SpawnGeneration {
        generation_id: GenerationId,
        generation_number: u64,
        cancellation: GenerationCancellation,
    },
    StopGeneration {
        generation_id: GenerationId,
        reason: StopReason,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FinalizeOutcome {
    pub applied: bool,
    pub actions: Vec<LifecycleAction>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GenerationSnapshot {
    pub id: GenerationId,
    pub number: u64,
    pub cancelled: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SupervisorSnapshot {
    pub state: SupervisorState,
    pub current: Option<GenerationSnapshot>,
    pub app_shutdown: bool,
    pub failure: Option<FailureReason>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Generation {
    id: GenerationId,
    number: u64,
    cancellation: GenerationCancellation,
}

pub struct CoreSupervisor {
    instance_nonce: u64,
    next_generation_number: u64,
    state: SupervisorState,
    current: Option<Generation>,
    app_shutdown: bool,
    restart_after_stop: bool,
    failure: Option<FailureReason>,
}

impl CoreSupervisor {
    pub fn new(instance_nonce: u64) -> Self {
        Self {
            instance_nonce,
            next_generation_number: 0,
            state: SupervisorState::Stopped,
            current: None,
            app_shutdown: false,
            restart_after_stop: false,
            failure: None,
        }
    }

    pub fn snapshot(&self) -> SupervisorSnapshot {
        SupervisorSnapshot {
            state: self.state,
            current: self.current.as_ref().map(|generation| GenerationSnapshot {
                id: generation.id,
                number: generation.number,
                cancelled: generation.cancellation.is_cancelled(),
            }),
            app_shutdown: self.app_shutdown,
            failure: self.failure,
        }
    }

    pub fn accepts_generation_callback(&self, generation_id: GenerationId) -> bool {
        self.state == SupervisorState::Running
            && self.current.as_ref().is_some_and(|generation| {
                generation.id == generation_id && !generation.cancellation.is_cancelled()
            })
    }

    pub fn submit(&mut self, intent: LifecycleIntent) -> Vec<LifecycleAction> {
        self.apply_intent(intent)
    }

    pub fn observe_spawn_succeeded(
        &mut self,
        generation_id: GenerationId,
    ) -> Option<SupervisorState> {
        if self.state != SupervisorState::Spawning
            || self.current.as_ref().map(|generation| generation.id) != Some(generation_id)
        {
            return None;
        }
        self.state = SupervisorState::Running;
        Some(self.state)
    }

    pub fn observe_generation_failed(
        &mut self,
        generation_id: GenerationId,
        reason: FailureReason,
    ) -> Vec<LifecycleAction> {
        if self.app_shutdown
            || self.current.as_ref().map(|generation| generation.id) != Some(generation_id)
            || !matches!(
                self.state,
                SupervisorState::Spawning | SupervisorState::Running
            )
        {
            return Vec::new();
        }
        if self.failure.is_none() {
            self.failure = Some(reason);
        }
        self.begin_stop(StopReason::Failure)
    }

    pub fn finalize_generation(&mut self, generation_id: GenerationId) -> FinalizeOutcome {
        if self.current.as_ref().map(|generation| generation.id) != Some(generation_id) {
            return FinalizeOutcome {
                applied: false,
                actions: Vec::new(),
            };
        }
        if let Some(generation) = self.current.as_ref() {
            generation.cancellation.cancel();
        }
        self.current = None;
        if self.app_shutdown {
            self.restart_after_stop = false;
            self.failure = None;
            self.state = SupervisorState::Stopped;
            return FinalizeOutcome {
                applied: true,
                actions: Vec::new(),
            };
        }
        if self.restart_after_stop {
            self.restart_after_stop = false;
            self.failure = None;
            return FinalizeOutcome {
                applied: true,
                actions: self.begin_spawn(),
            };
        }
        if self.failure.is_some() {
            self.state = SupervisorState::Failed;
        } else {
            self.state = SupervisorState::Stopped;
        }
        FinalizeOutcome {
            applied: true,
            actions: Vec::new(),
        }
    }

    fn apply_intent(&mut self, intent: LifecycleIntent) -> Vec<LifecycleAction> {
        match intent {
            LifecycleIntent::Start => {
                if self.app_shutdown
                    || self.current.is_some()
                    || self.state != SupervisorState::Stopped
                {
                    Vec::new()
                } else {
                    self.begin_spawn()
                }
            }
            LifecycleIntent::Stop => {
                self.restart_after_stop = false;
                self.failure = None;
                self.begin_stop(StopReason::User)
            }
            LifecycleIntent::Restart => {
                if self.app_shutdown {
                    Vec::new()
                } else if self.current.is_some() {
                    self.restart_after_stop = true;
                    self.failure = None;
                    self.begin_stop(StopReason::Restart)
                } else if self.state == SupervisorState::Stopped {
                    self.begin_spawn()
                } else {
                    Vec::new()
                }
            }
            LifecycleIntent::Retry => {
                if !self.app_shutdown
                    && self.current.is_none()
                    && self.state == SupervisorState::Failed
                {
                    self.failure = None;
                    self.begin_spawn()
                } else {
                    Vec::new()
                }
            }
            LifecycleIntent::AppShutdown => {
                self.app_shutdown = true;
                self.restart_after_stop = false;
                self.failure = None;
                self.begin_stop(StopReason::AppShutdown)
            }
        }
    }

    fn begin_spawn(&mut self) -> Vec<LifecycleAction> {
        self.failure = None;
        self.next_generation_number += 1;
        let number = self.next_generation_number;
        let id = GenerationId(((self.instance_nonce as u128) << 64) | number as u128);
        let cancellation = GenerationCancellation::new(id);
        self.current = Some(Generation {
            id,
            number,
            cancellation: cancellation.clone(),
        });
        self.state = SupervisorState::Spawning;
        vec![LifecycleAction::SpawnGeneration {
            generation_id: id,
            generation_number: number,
            cancellation,
        }]
    }

    fn begin_stop(&mut self, reason: StopReason) -> Vec<LifecycleAction> {
        let Some(generation) = self.current.as_mut() else {
            self.state = if self.failure.is_some() {
                SupervisorState::Failed
            } else {
                SupervisorState::Stopped
            };
            return Vec::new();
        };
        if self.state == SupervisorState::Stopping {
            return Vec::new();
        }
        generation.cancellation.cancel();
        self.state = SupervisorState::Stopping;
        vec![LifecycleAction::StopGeneration {
            generation_id: generation.id,
            reason,
        }]
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{
        CoreSupervisor, FailureReason, GenerationCancellation, GenerationId, LifecycleAction,
        LifecycleIntent, StopReason, SupervisorState,
    };
    #[cfg(windows)]
    use crate::managed_process_tree::{ManagedProcessSpec, ManagedProcessTree, WaitOutcome};

    fn spawned(supervisor: &mut CoreSupervisor) -> (GenerationId, u64, GenerationCancellation) {
        match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration {
                generation_id,
                generation_number,
                cancellation,
            }] => (*generation_id, *generation_number, cancellation.clone()),
            actions => panic!("start should spawn exactly one generation: {actions:?}"),
        }
    }

    fn running(supervisor: &mut CoreSupervisor) -> (GenerationId, u64) {
        let (generation_id, generation_number, _) = spawned(supervisor);
        assert_eq!(
            supervisor.observe_spawn_succeeded(generation_id),
            Some(SupervisorState::Running)
        );
        (generation_id, generation_number)
    }

    #[test]
    fn explicit_restart_waits_for_old_generation_cleanup() {
        let mut supervisor = CoreSupervisor::new(0xA11CE);
        let (first_id, first_number, cancellation) = spawned(&mut supervisor);

        assert_eq!(
            supervisor.submit(LifecycleIntent::Restart),
            vec![LifecycleAction::StopGeneration {
                generation_id: first_id,
                reason: StopReason::Restart,
            }]
        );
        assert!(cancellation.is_cancelled());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopping);
        assert!(supervisor.submit(LifecycleIntent::Restart).is_empty());

        let finalized = supervisor.finalize_generation(first_id);
        assert!(finalized.applied);
        let (second_id, second_number) = match finalized.actions.as_slice() {
            [LifecycleAction::SpawnGeneration {
                generation_id,
                generation_number,
                ..
            }] => (*generation_id, *generation_number),
            actions => panic!("cleanup should release one explicit restart: {actions:?}"),
        };
        assert_ne!(second_id, first_id);
        assert_eq!(second_number, first_number + 1);
        assert_eq!(supervisor.snapshot().state, SupervisorState::Spawning);
    }

    #[test]
    fn failure_invalidates_generation_preserves_first_cause_and_waits_for_cleanup() {
        let mut supervisor = CoreSupervisor::new(0xFA11);
        let (generation_id, _, cancellation) = spawned(&mut supervisor);
        assert_eq!(
            supervisor.observe_spawn_succeeded(generation_id),
            Some(SupervisorState::Running)
        );

        assert_eq!(
            supervisor.observe_generation_failed(generation_id, FailureReason::ConnectionLost,),
            vec![LifecycleAction::StopGeneration {
                generation_id,
                reason: StopReason::Failure,
            }]
        );
        assert!(cancellation.is_cancelled());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopping);
        assert_eq!(
            supervisor.snapshot().failure,
            Some(FailureReason::ConnectionLost)
        );

        assert!(supervisor
            .observe_generation_failed(generation_id, FailureReason::UnexpectedExit)
            .is_empty());
        assert_eq!(
            supervisor.snapshot().failure,
            Some(FailureReason::ConnectionLost)
        );
        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());

        let finalized = supervisor.finalize_generation(generation_id);
        assert!(finalized.applied);
        assert!(finalized.actions.is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Failed);
        assert!(supervisor.snapshot().current.is_none());
        assert_eq!(
            supervisor.snapshot().failure,
            Some(FailureReason::ConnectionLost)
        );
    }

    #[test]
    fn manual_retry_only_starts_once_from_failed() {
        let mut supervisor = CoreSupervisor::new(0xBEEF);
        let (failed_id, failed_number, _) = spawned(&mut supervisor);
        let _ =
            supervisor.observe_generation_failed(failed_id, FailureReason::TemporarySpawnFailure);
        let _ = supervisor.finalize_generation(failed_id);
        assert_eq!(supervisor.snapshot().state, SupervisorState::Failed);

        assert!(supervisor.submit(LifecycleIntent::Start).is_empty());
        assert!(supervisor.submit(LifecycleIntent::Restart).is_empty());
        let (retry_id, retry_number) = match supervisor.submit(LifecycleIntent::Retry).as_slice() {
            [LifecycleAction::SpawnGeneration {
                generation_id,
                generation_number,
                ..
            }] => (*generation_id, *generation_number),
            actions => panic!("manual retry should create one generation: {actions:?}"),
        };
        assert_ne!(retry_id, failed_id);
        assert_eq!(retry_number, failed_number + 1);
        assert_eq!(supervisor.snapshot().failure, None);
        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());
    }

    #[test]
    fn retry_does_not_restart_a_running_or_stopping_generation() {
        let mut supervisor = CoreSupervisor::new(0x1234);
        let (generation_id, _) = running(&mut supervisor);

        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Running);
        let _ = supervisor.submit(LifecycleIntent::Restart);
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopping);
        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());

        let finalized = supervisor.finalize_generation(generation_id);
        assert_eq!(finalized.actions.len(), 1);
    }

    #[test]
    fn explicit_stop_overrides_a_restart_queued_during_cleanup() {
        let mut supervisor = CoreSupervisor::new(0xCAFE);
        let (generation_id, _) = running(&mut supervisor);
        assert_eq!(supervisor.submit(LifecycleIntent::Restart).len(), 1);
        assert!(supervisor.submit(LifecycleIntent::Stop).is_empty());

        let finalized = supervisor.finalize_generation(generation_id);
        assert!(finalized.applied);
        assert!(finalized.actions.is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopped);
        assert_eq!(supervisor.snapshot().failure, None);
    }

    #[test]
    fn app_shutdown_is_permanent_and_never_spawns_after_cleanup() {
        let mut supervisor = CoreSupervisor::new(0xC105E);
        let (generation_id, _, cancellation) = spawned(&mut supervisor);

        assert_eq!(
            supervisor.submit(LifecycleIntent::AppShutdown),
            vec![LifecycleAction::StopGeneration {
                generation_id,
                reason: StopReason::AppShutdown,
            }]
        );
        assert!(cancellation.is_cancelled());
        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());
        assert!(supervisor.submit(LifecycleIntent::Restart).is_empty());

        let finalized = supervisor.finalize_generation(generation_id);
        assert!(finalized.applied);
        assert!(finalized.actions.is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopped);
        assert!(supervisor.snapshot().app_shutdown);
        assert!(supervisor.submit(LifecycleIntent::Start).is_empty());
    }

    #[test]
    fn stale_callbacks_and_duplicate_finalize_cannot_mutate_new_generation() {
        let mut supervisor = CoreSupervisor::new(0x5157);
        let (first_id, _) = running(&mut supervisor);
        let _ = supervisor.submit(LifecycleIntent::Restart);
        let finalized = supervisor.finalize_generation(first_id);
        let second_id = match finalized.actions.as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("restart should spawn once: {actions:?}"),
        };

        assert!(!supervisor.finalize_generation(first_id).applied);
        assert_eq!(supervisor.observe_spawn_succeeded(first_id), None);
        assert!(supervisor
            .observe_generation_failed(first_id, FailureReason::UnexpectedExit)
            .is_empty());
        assert_eq!(
            supervisor.observe_spawn_succeeded(second_id),
            Some(SupervisorState::Running)
        );
        assert!(supervisor.accepts_generation_callback(second_id));
        assert!(!supervisor.accepts_generation_callback(first_id));
    }

    #[test]
    fn generation_cancellation_tokens_are_independent() {
        let mut supervisor = CoreSupervisor::new(0xC0DE);
        let (first_id, _, first_token) = spawned(&mut supervisor);
        let _ = supervisor.submit(LifecycleIntent::Restart);
        let finalized = supervisor.finalize_generation(first_id);
        let (second_id, second_token) = match finalized.actions.as_slice() {
            [LifecycleAction::SpawnGeneration {
                generation_id,
                cancellation,
                ..
            }] => (*generation_id, cancellation.clone()),
            actions => panic!("restart should spawn once: {actions:?}"),
        };

        assert!(first_token.is_cancelled());
        assert!(!second_token.is_cancelled());
        assert_eq!(first_token.generation_id(), first_id);
        assert_eq!(second_token.generation_id(), second_id);
    }

    #[cfg(windows)]
    fn holding_process_spec() -> ManagedProcessSpec {
        let mut spec = ManagedProcessSpec::new(
            std::env::current_exe().expect("current Rust test executable should resolve"),
        );
        spec.arg("--ignored")
            .arg("--exact")
            .arg("managed_process_tree::tests::fixture_holds")
            .arg("--nocapture");
        spec
    }

    #[cfg(windows)]
    fn stop_real_tree(tree: &mut ManagedProcessTree) {
        tree.terminate_tree(91)
            .expect("Supervisor stop action should terminate the real Job tree");
        assert!(matches!(
            tree.wait(Duration::from_secs(3))
                .expect("terminated root should become observable"),
            WaitOutcome::Exited(_)
        ));
        assert!(tree
            .verify_tree_exited(Duration::from_secs(3))
            .expect("Job ActiveProcesses should query"));
        tree.release_exited_handles()
            .expect("stopped generation handles should release");
    }

    #[cfg(windows)]
    #[test]
    fn real_process_tree_obeys_serial_restart_and_app_shutdown() {
        let mut supervisor = CoreSupervisor::new(0xD15C);
        let (first_id, _) = running(&mut supervisor);
        let mut first_tree =
            ManagedProcessTree::spawn(&holding_process_spec()).expect("first Job spawn");

        let stop = supervisor.submit(LifecycleIntent::Restart);
        assert_eq!(
            stop,
            vec![LifecycleAction::StopGeneration {
                generation_id: first_id,
                reason: StopReason::Restart,
            }]
        );
        stop_real_tree(&mut first_tree);
        let second_id = match supervisor.finalize_generation(first_id).actions.as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("restart should wait for cleanup: {actions:?}"),
        };
        assert_eq!(
            supervisor.observe_spawn_succeeded(second_id),
            Some(SupervisorState::Running)
        );

        let mut second_tree =
            ManagedProcessTree::spawn(&holding_process_spec()).expect("second Job spawn");
        assert_eq!(
            supervisor.submit(LifecycleIntent::AppShutdown),
            vec![LifecycleAction::StopGeneration {
                generation_id: second_id,
                reason: StopReason::AppShutdown,
            }]
        );
        stop_real_tree(&mut second_tree);
        assert!(supervisor.finalize_generation(second_id).actions.is_empty());
        assert!(supervisor.snapshot().app_shutdown);
    }
}
