use std::{
    collections::VecDeque,
    fmt,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};

const AUTOMATIC_RESTART_BACKOFFS: [Duration; 3] = [
    Duration::from_millis(250),
    Duration::from_secs(1),
    Duration::from_secs(3),
];

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
    Exited,
    Restarting,
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
    Recovery,
    AppShutdown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct RestartToken(u64);

impl RestartToken {
    pub fn as_u64(self) -> u64 {
        self.0
    }
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

impl FailureReason {
    pub fn is_automatically_retryable(self) -> bool {
        matches!(
            self,
            Self::UnexpectedExit
                | Self::TemporarySpawnFailure
                | Self::HelloTimeout
                | Self::InitializeTimeout
                | Self::ConnectionLost
        )
    }
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
    ScheduleRestart {
        token: RestartToken,
        delay: Duration,
    },
    CancelRestart {
        token: RestartToken,
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
    pub restart_pending: bool,
    pub automatic_restart_attempts: u8,
    pub scheduled_restart: Option<RestartToken>,
    pub last_failure: Option<FailureReason>,
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
    restart_pending: bool,
    automatic_restart_attempts: u8,
    next_restart_token: u64,
    scheduled_restart: Option<RestartToken>,
    pending_failure: Option<FailureReason>,
    last_failure: Option<FailureReason>,
    intents: VecDeque<LifecycleIntent>,
}

impl CoreSupervisor {
    pub fn new(instance_nonce: u64) -> Self {
        Self {
            instance_nonce,
            next_generation_number: 0,
            state: SupervisorState::Stopped,
            current: None,
            app_shutdown: false,
            restart_pending: false,
            automatic_restart_attempts: 0,
            next_restart_token: 0,
            scheduled_restart: None,
            pending_failure: None,
            last_failure: None,
            intents: VecDeque::new(),
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
            restart_pending: self.restart_pending,
            automatic_restart_attempts: self.automatic_restart_attempts,
            scheduled_restart: self.scheduled_restart,
            last_failure: self.last_failure,
        }
    }

    pub fn accepts_generation_callback(&self, generation_id: GenerationId) -> bool {
        self.state == SupervisorState::Running
            && self.current.as_ref().is_some_and(|generation| {
                generation.id == generation_id && !generation.cancellation.is_cancelled()
            })
    }

    pub fn submit(&mut self, intent: LifecycleIntent) -> Vec<LifecycleAction> {
        self.intents.push_back(intent);
        let mut actions = Vec::new();
        while let Some(next) = self.intents.pop_front() {
            actions.extend(self.apply_intent(next));
        }
        actions
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

    pub fn observe_spawn_failed(&mut self, generation_id: GenerationId) -> Vec<LifecycleAction> {
        if self.current.as_ref().map(|generation| generation.id) != Some(generation_id) {
            return Vec::new();
        }
        if self.state == SupervisorState::Stopping {
            return Vec::new();
        }
        if self.state != SupervisorState::Spawning {
            return Vec::new();
        }
        if let Some(generation) = self.current.as_ref() {
            generation.cancellation.cancel();
        }
        self.current = None;
        self.state = SupervisorState::Exited;
        Vec::new()
    }

    pub fn observe_generation_stopped(
        &mut self,
        generation_id: GenerationId,
    ) -> Vec<LifecycleAction> {
        self.finalize_generation(generation_id).actions
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
        self.pending_failure = Some(reason);
        self.last_failure = Some(reason);
        self.begin_stop(StopReason::Recovery)
    }

    pub fn observe_restart_timer(&mut self, token: RestartToken) -> Vec<LifecycleAction> {
        if self.app_shutdown
            || self.state != SupervisorState::Restarting
            || self.scheduled_restart != Some(token)
        {
            return Vec::new();
        }
        self.scheduled_restart = None;
        self.begin_spawn()
    }

    pub fn finalize_generation(&mut self, generation_id: GenerationId) -> FinalizeOutcome {
        if self.current.as_ref().map(|generation| generation.id) != Some(generation_id) {
            return FinalizeOutcome {
                applied: false,
                actions: Vec::new(),
            };
        }
        let completed_stop_workflow = self.state == SupervisorState::Stopping;
        if let Some(generation) = self.current.as_ref() {
            generation.cancellation.cancel();
        }
        self.current = None;
        if self.app_shutdown {
            self.restart_pending = false;
            self.pending_failure = None;
            self.state = SupervisorState::Stopped;
            return FinalizeOutcome {
                applied: true,
                actions: Vec::new(),
            };
        }
        if completed_stop_workflow && self.restart_pending {
            self.restart_pending = false;
            self.pending_failure = None;
            return FinalizeOutcome {
                applied: true,
                actions: self.begin_spawn(),
            };
        }
        if completed_stop_workflow {
            if let Some(reason) = self.pending_failure.take() {
                if reason.is_automatically_retryable()
                    && usize::from(self.automatic_restart_attempts)
                        < AUTOMATIC_RESTART_BACKOFFS.len()
                {
                    let delay =
                        AUTOMATIC_RESTART_BACKOFFS[usize::from(self.automatic_restart_attempts)];
                    self.automatic_restart_attempts += 1;
                    self.next_restart_token += 1;
                    let token = RestartToken(self.next_restart_token);
                    self.scheduled_restart = Some(token);
                    self.state = SupervisorState::Restarting;
                    return FinalizeOutcome {
                        applied: true,
                        actions: vec![LifecycleAction::ScheduleRestart { token, delay }],
                    };
                }
                self.state = SupervisorState::Failed;
                return FinalizeOutcome {
                    applied: true,
                    actions: Vec::new(),
                };
            }
            self.state = SupervisorState::Stopped;
        } else {
            self.state = SupervisorState::Exited;
        }
        FinalizeOutcome {
            applied: true,
            actions: Vec::new(),
        }
    }

    fn apply_intent(&mut self, intent: LifecycleIntent) -> Vec<LifecycleAction> {
        match intent {
            LifecycleIntent::Start => {
                if self.app_shutdown || self.current.is_some() || self.scheduled_restart.is_some() {
                    Vec::new()
                } else {
                    self.begin_spawn()
                }
            }
            LifecycleIntent::Stop => {
                self.restart_pending = false;
                self.pending_failure = None;
                let mut actions = self.cancel_scheduled_restart();
                actions.extend(self.begin_stop(StopReason::User));
                actions
            }
            LifecycleIntent::Restart => {
                if self.app_shutdown {
                    Vec::new()
                } else if self.current.is_some() {
                    self.restart_pending = true;
                    self.begin_stop(StopReason::Restart)
                } else {
                    let mut actions = self.cancel_scheduled_restart();
                    actions.extend(self.begin_spawn());
                    actions
                }
            }
            LifecycleIntent::Retry => self.manual_retry(),
            LifecycleIntent::AppShutdown => {
                self.app_shutdown = true;
                self.restart_pending = false;
                self.pending_failure = None;
                let mut actions = self.cancel_scheduled_restart();
                actions.extend(self.begin_stop(StopReason::AppShutdown));
                actions
            }
        }
    }

    fn manual_retry(&mut self) -> Vec<LifecycleAction> {
        if self.app_shutdown {
            return Vec::new();
        }
        if self.current.is_some() {
            if self.state == SupervisorState::Stopping {
                self.restart_pending = true;
                self.pending_failure = None;
                self.automatic_restart_attempts = 0;
                return Vec::new();
            }
            if self.state == SupervisorState::Running {
                self.restart_pending = true;
                self.pending_failure = None;
                self.automatic_restart_attempts = 0;
                return self.begin_stop(StopReason::Restart);
            }
            return Vec::new();
        }
        self.pending_failure = None;
        self.restart_pending = false;
        self.automatic_restart_attempts = 0;
        let mut actions = self.cancel_scheduled_restart();
        actions.extend(self.begin_spawn());
        actions
    }

    fn cancel_scheduled_restart(&mut self) -> Vec<LifecycleAction> {
        let Some(token) = self.scheduled_restart.take() else {
            return Vec::new();
        };
        self.state = SupervisorState::Stopped;
        vec![LifecycleAction::CancelRestart { token }]
    }

    fn begin_spawn(&mut self) -> Vec<LifecycleAction> {
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
            self.state = SupervisorState::Stopped;
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
        CoreSupervisor, FailureReason, LifecycleAction, LifecycleIntent, StopReason,
        SupervisorState,
    };
    #[cfg(windows)]
    use crate::managed_process_tree::{ManagedProcessSpec, ManagedProcessTree, WaitOutcome};

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

    #[test]
    fn restart_during_spawn_waits_for_old_generation_cleanup_before_spawning_again() {
        let mut supervisor = CoreSupervisor::new(0x5a6b_7c8d_9eaf_1021);

        let first_actions = supervisor.submit(LifecycleIntent::Start);
        let (first_id, first_number) = match first_actions.as_slice() {
            [LifecycleAction::SpawnGeneration {
                generation_id,
                generation_number,
                ..
            }] => (*generation_id, *generation_number),
            actions => panic!("expected one spawn action, got {actions:?}"),
        };
        assert_eq!(first_number, 1);
        assert_ne!(first_id.as_u128(), first_number as u128);
        assert_eq!(supervisor.snapshot().state, SupervisorState::Spawning);

        assert_eq!(
            supervisor.submit(LifecycleIntent::Restart),
            vec![LifecycleAction::StopGeneration {
                generation_id: first_id,
                reason: StopReason::Restart,
            }]
        );
        let stopping = supervisor.snapshot();
        assert_eq!(stopping.state, SupervisorState::Stopping);
        assert!(
            stopping
                .current
                .expect("generation should remain owned")
                .cancelled
        );

        assert!(supervisor.submit(LifecycleIntent::Restart).is_empty());
        assert!(supervisor.observe_spawn_succeeded(first_id).is_none());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopping);

        let next_actions = supervisor.observe_generation_stopped(first_id);
        let (second_id, second_number) = match next_actions.as_slice() {
            [LifecycleAction::SpawnGeneration {
                generation_id,
                generation_number,
                ..
            }] => (*generation_id, *generation_number),
            actions => panic!("old cleanup should release exactly one queued restart: {actions:?}"),
        };
        assert_eq!(second_number, 2);
        assert_ne!(second_id, first_id);
        assert_eq!(supervisor.snapshot().state, SupervisorState::Spawning);

        assert!(supervisor.observe_spawn_succeeded(first_id).is_none());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Spawning);
        assert_eq!(
            supervisor.observe_spawn_succeeded(second_id),
            Some(SupervisorState::Running)
        );
        assert_eq!(supervisor.snapshot().state, SupervisorState::Running);
    }

    #[test]
    fn each_spawn_action_exposes_an_independent_cancellation_token() {
        let mut supervisor = CoreSupervisor::new(0x3141_5926_5358_9793);
        let (first_id, first_cancellation) =
            match supervisor.submit(LifecycleIntent::Start).as_slice() {
                [LifecycleAction::SpawnGeneration {
                    generation_id,
                    cancellation,
                    ..
                }] => (*generation_id, cancellation.clone()),
                actions => panic!("expected first spawn, got {actions:?}"),
            };
        assert_eq!(first_cancellation.generation_id(), first_id);
        assert!(!first_cancellation.is_cancelled());

        assert_eq!(supervisor.submit(LifecycleIntent::Restart).len(), 1);
        assert!(first_cancellation.is_cancelled());
        let (second_id, second_cancellation) =
            match supervisor.observe_generation_stopped(first_id).as_slice() {
                [LifecycleAction::SpawnGeneration {
                    generation_id,
                    cancellation,
                    ..
                }] => (*generation_id, cancellation.clone()),
                actions => panic!("expected replacement spawn, got {actions:?}"),
            };
        assert_ne!(second_id, first_id);
        assert_eq!(second_cancellation.generation_id(), second_id);
        assert!(!second_cancellation.is_cancelled());
        assert!(first_cancellation.is_cancelled());
    }

    #[test]
    fn app_shutdown_during_spawn_is_permanent_and_late_callbacks_are_stale() {
        let mut supervisor = CoreSupervisor::new(0x1111_2222_3333_4444);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };

        assert_eq!(
            supervisor.submit(LifecycleIntent::AppShutdown),
            vec![LifecycleAction::StopGeneration {
                generation_id,
                reason: StopReason::AppShutdown,
            }]
        );
        assert!(supervisor.snapshot().app_shutdown);
        assert!(supervisor.submit(LifecycleIntent::Restart).is_empty());
        assert!(supervisor.submit(LifecycleIntent::AppShutdown).is_empty());
        assert!(supervisor.observe_spawn_succeeded(generation_id).is_none());

        assert!(supervisor
            .observe_generation_stopped(generation_id)
            .is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopped);
        assert!(supervisor.submit(LifecycleIntent::Start).is_empty());
        assert!(supervisor.submit(LifecycleIntent::Restart).is_empty());
        assert!(supervisor
            .observe_generation_stopped(generation_id)
            .is_empty());
    }

    #[test]
    fn spawn_failure_is_generation_scoped_and_requires_an_explicit_new_start() {
        let mut supervisor = CoreSupervisor::new(0x5555_6666_7777_8888);
        let first_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };

        assert!(supervisor.observe_spawn_failed(first_id).is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Exited);
        assert!(supervisor.snapshot().current.is_none());

        let second_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("manual start should create a new generation: {actions:?}"),
        };
        assert_ne!(second_id, first_id);
        assert!(supervisor.observe_spawn_failed(first_id).is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Spawning);
        assert_eq!(
            supervisor.observe_spawn_succeeded(second_id),
            Some(SupervisorState::Running)
        );
    }

    #[test]
    fn unexpected_current_generation_exit_is_not_reported_as_an_orderly_stop() {
        let mut supervisor = CoreSupervisor::new(0x0bad_f00d_dead_beef);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        assert_eq!(
            supervisor.observe_spawn_succeeded(generation_id),
            Some(SupervisorState::Running)
        );

        assert!(supervisor
            .observe_generation_stopped(generation_id)
            .is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Exited);
        assert!(supervisor.snapshot().current.is_none());
        assert!(!supervisor.accepts_generation_callback(generation_id));
        assert!(supervisor
            .observe_generation_stopped(generation_id)
            .is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Exited);
    }

    #[test]
    fn every_terminal_generation_event_cancels_the_generation_token() {
        let mut supervisor = CoreSupervisor::new(0x4242_5151_6060_7878);

        let (failed_id, failed_cancellation) =
            match supervisor.submit(LifecycleIntent::Start).as_slice() {
                [LifecycleAction::SpawnGeneration {
                    generation_id,
                    cancellation,
                    ..
                }] => (*generation_id, cancellation.clone()),
                actions => panic!("expected failed generation spawn, got {actions:?}"),
            };
        assert!(supervisor.observe_spawn_failed(failed_id).is_empty());
        assert!(
            failed_cancellation.is_cancelled(),
            "spawn failure must wake generation-scoped workers"
        );

        let (exited_id, exited_cancellation) =
            match supervisor.submit(LifecycleIntent::Start).as_slice() {
                [LifecycleAction::SpawnGeneration {
                    generation_id,
                    cancellation,
                    ..
                }] => (*generation_id, cancellation.clone()),
                actions => panic!("expected exited generation spawn, got {actions:?}"),
            };
        supervisor.observe_spawn_succeeded(exited_id);
        assert!(supervisor.observe_generation_stopped(exited_id).is_empty());
        assert!(
            exited_cancellation.is_cancelled(),
            "unexpected exit must wake generation-scoped workers"
        );

        let (stopped_id, stopped_cancellation) =
            match supervisor.submit(LifecycleIntent::Start).as_slice() {
                [LifecycleAction::SpawnGeneration {
                    generation_id,
                    cancellation,
                    ..
                }] => (*generation_id, cancellation.clone()),
                actions => panic!("expected stopped generation spawn, got {actions:?}"),
            };
        supervisor.observe_spawn_succeeded(stopped_id);
        assert_eq!(supervisor.submit(LifecycleIntent::Stop).len(), 1);
        assert!(stopped_cancellation.is_cancelled());
        assert!(supervisor.observe_generation_stopped(stopped_id).is_empty());
        assert!(stopped_cancellation.is_cancelled());
    }

    #[test]
    fn repeated_stop_and_finalize_emit_only_one_stop_workflow() {
        let mut supervisor = CoreSupervisor::new(0x9999_aaaa_bbbb_cccc);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        assert_eq!(
            supervisor.observe_spawn_succeeded(generation_id),
            Some(SupervisorState::Running)
        );

        assert_eq!(
            supervisor.submit(LifecycleIntent::Stop),
            vec![LifecycleAction::StopGeneration {
                generation_id,
                reason: StopReason::User,
            }]
        );
        assert!(supervisor.submit(LifecycleIntent::Stop).is_empty());
        assert!(supervisor.submit(LifecycleIntent::Start).is_empty());
        assert!(supervisor
            .observe_generation_stopped(generation_id)
            .is_empty());
        assert!(supervisor
            .observe_generation_stopped(generation_id)
            .is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopped);
    }

    #[test]
    fn duplicate_finalize_reports_exactly_one_applied_transition() {
        let mut supervisor = CoreSupervisor::new(0xabcd_abcd_abcd_abcd);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        supervisor.observe_spawn_succeeded(generation_id);
        supervisor.submit(LifecycleIntent::Stop);

        let first = supervisor.finalize_generation(generation_id);
        assert!(first.applied);
        assert!(first.actions.is_empty());
        let duplicate = supervisor.finalize_generation(generation_id);
        assert!(!duplicate.applied);
        assert!(duplicate.actions.is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopped);
    }

    #[test]
    fn only_the_current_running_generation_can_publish_callbacks() {
        let mut supervisor = CoreSupervisor::new(0xdddd_eeee_ffff_0001);
        let first_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        assert!(!supervisor.accepts_generation_callback(first_id));
        assert_eq!(
            supervisor.observe_spawn_succeeded(first_id),
            Some(SupervisorState::Running)
        );
        assert!(supervisor.accepts_generation_callback(first_id));

        assert_eq!(supervisor.submit(LifecycleIntent::Restart).len(), 1);
        assert!(!supervisor.accepts_generation_callback(first_id));
        let second_id = match supervisor.observe_generation_stopped(first_id).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected replacement spawn, got {actions:?}"),
        };
        assert!(!supervisor.accepts_generation_callback(first_id));
        assert!(!supervisor.accepts_generation_callback(second_id));
        assert_eq!(
            supervisor.observe_spawn_succeeded(second_id),
            Some(SupervisorState::Running)
        );
        assert!(!supervisor.accepts_generation_callback(first_id));
        assert!(supervisor.accepts_generation_callback(second_id));
    }

    #[test]
    fn explicit_stop_overrides_a_restart_queued_during_stopping() {
        let mut supervisor = CoreSupervisor::new(0x1234_5678_9abc_def0);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        supervisor.observe_spawn_succeeded(generation_id);

        assert_eq!(supervisor.submit(LifecycleIntent::Restart).len(), 1);
        assert!(supervisor.snapshot().restart_pending);
        assert!(supervisor.submit(LifecycleIntent::Stop).is_empty());
        assert!(!supervisor.snapshot().restart_pending);
        assert!(supervisor
            .observe_generation_stopped(generation_id)
            .is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopped);
    }

    #[test]
    fn spawn_failure_during_stop_does_not_bypass_the_generation_cleanup_barrier() {
        let mut supervisor = CoreSupervisor::new(0x2468_ace0_1357_bdf1);
        let first_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        assert_eq!(supervisor.submit(LifecycleIntent::Restart).len(), 1);

        assert!(supervisor.observe_spawn_failed(first_id).is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopping);
        assert_eq!(
            supervisor
                .snapshot()
                .current
                .map(|generation| generation.id),
            Some(first_id)
        );

        let replacement = supervisor.observe_generation_stopped(first_id);
        assert!(matches!(
            replacement.as_slice(),
            [LifecycleAction::SpawnGeneration {
                generation_number: 2,
                ..
            }]
        ));
    }

    #[test]
    fn retryable_failures_use_bounded_backoff_and_stop_after_three_attempts() {
        let mut supervisor = CoreSupervisor::new(0x1b04_0000_0000_0001);
        let mut generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        supervisor.observe_spawn_succeeded(generation_id);

        for (attempt, expected_delay) in [
            Duration::from_millis(250),
            Duration::from_secs(1),
            Duration::from_secs(3),
        ]
        .into_iter()
        .enumerate()
        {
            assert_eq!(
                supervisor.observe_generation_failed(generation_id, FailureReason::UnexpectedExit,),
                vec![LifecycleAction::StopGeneration {
                    generation_id,
                    reason: StopReason::Recovery,
                }]
            );
            let scheduled = supervisor.finalize_generation(generation_id);
            let token = match scheduled.actions.as_slice() {
                [LifecycleAction::ScheduleRestart { token, delay }] if *delay == expected_delay => {
                    *token
                }
                actions => panic!("attempt {attempt} should schedule bounded backoff: {actions:?}"),
            };
            assert_eq!(
                supervisor.snapshot().automatic_restart_attempts,
                (attempt + 1) as u8
            );
            generation_id = match supervisor.observe_restart_timer(token).as_slice() {
                [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
                actions => panic!("current restart token should spawn once: {actions:?}"),
            };
            supervisor.observe_spawn_succeeded(generation_id);
        }

        assert_eq!(
            supervisor.observe_generation_failed(generation_id, FailureReason::UnexpectedExit),
            vec![LifecycleAction::StopGeneration {
                generation_id,
                reason: StopReason::Recovery,
            }]
        );
        assert!(supervisor
            .finalize_generation(generation_id)
            .actions
            .is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Failed);
        assert_eq!(supervisor.snapshot().automatic_restart_attempts, 3);
    }

    #[test]
    fn app_shutdown_cancels_backoff_and_stale_timer_cannot_spawn() {
        let mut supervisor = CoreSupervisor::new(0x1b04_0000_0000_0002);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        let _ = supervisor.observe_spawn_succeeded(generation_id);
        let _ = supervisor.observe_generation_failed(generation_id, FailureReason::HelloTimeout);
        let token = match supervisor
            .finalize_generation(generation_id)
            .actions
            .as_slice()
        {
            [LifecycleAction::ScheduleRestart { token, .. }] => *token,
            actions => panic!("retryable hello timeout should schedule restart: {actions:?}"),
        };

        assert_eq!(
            supervisor.submit(LifecycleIntent::AppShutdown),
            vec![LifecycleAction::CancelRestart { token }]
        );
        assert!(supervisor.observe_restart_timer(token).is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopped);
        assert!(supervisor.snapshot().app_shutdown);
    }

    #[test]
    fn repeated_manual_retry_while_stopping_coalesces_and_resets_budget() {
        let mut supervisor = CoreSupervisor::new(0x1b04_0000_0000_0003);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        let _ = supervisor.observe_spawn_succeeded(generation_id);
        let _ = supervisor.observe_generation_failed(generation_id, FailureReason::UnexpectedExit);

        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());
        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());
        let actions = supervisor.finalize_generation(generation_id).actions;
        assert!(matches!(
            actions.as_slice(),
            [LifecycleAction::SpawnGeneration {
                generation_number: 2,
                ..
            }]
        ));
        assert_eq!(supervisor.snapshot().automatic_restart_attempts, 0);
    }

    #[test]
    fn repeated_manual_retry_from_running_submits_one_serial_restart() {
        let mut supervisor = CoreSupervisor::new(0x1d01_0000_0000_0001);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        supervisor.observe_spawn_succeeded(generation_id);

        assert_eq!(
            supervisor.submit(LifecycleIntent::Retry),
            vec![LifecycleAction::StopGeneration {
                generation_id,
                reason: StopReason::Restart,
            }]
        );
        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());
        assert!(supervisor.snapshot().restart_pending);

        let replacement = supervisor.finalize_generation(generation_id).actions;
        assert!(matches!(
            replacement.as_slice(),
            [LifecycleAction::SpawnGeneration {
                generation_number: 2,
                ..
            }]
        ));
    }

    #[test]
    fn deterministic_failure_never_auto_retries_but_manual_retry_can_start_once() {
        let mut supervisor = CoreSupervisor::new(0x1b04_0000_0000_0004);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        let _ = supervisor.observe_spawn_succeeded(generation_id);
        let _ = supervisor
            .observe_generation_failed(generation_id, FailureReason::ProtocolMajorIncompatible);
        assert!(supervisor
            .finalize_generation(generation_id)
            .actions
            .is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Failed);

        let retry = supervisor.submit(LifecycleIntent::Retry);
        assert!(matches!(
            retry.as_slice(),
            [LifecycleAction::SpawnGeneration {
                generation_number: 2,
                ..
            }]
        ));
        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());
    }

    #[test]
    fn manual_retry_during_backoff_cancels_old_timer_before_spawning() {
        let mut supervisor = CoreSupervisor::new(0x1b04_0000_0000_0005);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        let _ = supervisor.observe_spawn_succeeded(generation_id);
        let _ = supervisor.observe_generation_failed(generation_id, FailureReason::ConnectionLost);
        let token = match supervisor
            .finalize_generation(generation_id)
            .actions
            .as_slice()
        {
            [LifecycleAction::ScheduleRestart { token, .. }] => *token,
            actions => panic!("connection loss should schedule restart: {actions:?}"),
        };

        let retry = supervisor.submit(LifecycleIntent::Retry);
        assert!(matches!(
            retry.as_slice(),
            [
                LifecycleAction::CancelRestart {
                    token: cancelled_token
                },
                LifecycleAction::SpawnGeneration {
                    generation_number: 2,
                    ..
                }
            ] if *cancelled_token == token
        ));
        assert!(supervisor.observe_restart_timer(token).is_empty());
        assert_eq!(supervisor.snapshot().automatic_restart_attempts, 0);
    }

    #[test]
    fn explicit_restart_during_backoff_cancels_old_timer_without_resetting_budget() {
        let mut supervisor = CoreSupervisor::new(0x1b04_0000_0000_0007);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        let _ = supervisor.observe_generation_failed(generation_id, FailureReason::HelloTimeout);
        let token = match supervisor
            .finalize_generation(generation_id)
            .actions
            .as_slice()
        {
            [LifecycleAction::ScheduleRestart { token, .. }] => *token,
            actions => panic!("hello timeout should schedule restart: {actions:?}"),
        };

        let restart = supervisor.submit(LifecycleIntent::Restart);
        assert!(matches!(
            restart.as_slice(),
            [
                LifecycleAction::CancelRestart {
                    token: cancelled_token
                },
                LifecycleAction::SpawnGeneration {
                    generation_number: 2,
                    ..
                }
            ] if *cancelled_token == token
        ));
        assert!(supervisor.observe_restart_timer(token).is_empty());
        assert_eq!(supervisor.snapshot().automatic_restart_attempts, 1);
        assert!(supervisor.snapshot().scheduled_restart.is_none());
    }

    #[test]
    fn failure_classification_matches_the_frozen_adr_boundary() {
        for reason in [
            FailureReason::UnexpectedExit,
            FailureReason::TemporarySpawnFailure,
            FailureReason::HelloTimeout,
            FailureReason::InitializeTimeout,
            FailureReason::ConnectionLost,
        ] {
            assert!(reason.is_automatically_retryable(), "{reason:?}");
        }
        for reason in [
            FailureReason::ProtocolMajorIncompatible,
            FailureReason::MissingRequiredCapability,
            FailureReason::SetupRequired,
            FailureReason::DeterministicConfiguration,
            FailureReason::DeterministicRuntime,
            FailureReason::SecurityBoundary,
        ] {
            assert!(!reason.is_automatically_retryable(), "{reason:?}");
        }
    }

    #[test]
    fn app_shutdown_during_recovery_stop_discards_a_queued_manual_retry() {
        let mut supervisor = CoreSupervisor::new(0x1b04_0000_0000_0006);
        let generation_id = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration { generation_id, .. }] => *generation_id,
            actions => panic!("expected initial spawn, got {actions:?}"),
        };
        let _ = supervisor
            .observe_generation_failed(generation_id, FailureReason::TemporarySpawnFailure);
        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());
        assert!(supervisor.snapshot().restart_pending);

        assert!(supervisor.submit(LifecycleIntent::AppShutdown).is_empty());
        assert!(supervisor
            .finalize_generation(generation_id)
            .actions
            .is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopped);
        assert!(!supervisor.snapshot().restart_pending);
        assert!(supervisor.submit(LifecycleIntent::Retry).is_empty());
    }

    #[cfg(windows)]
    #[test]
    fn real_managed_process_tree_obeys_serial_restart_and_app_shutdown_actions() {
        let mut supervisor = CoreSupervisor::new(0xfeed_face_cafe_beef);
        let (first_id, first_number) = match supervisor.submit(LifecycleIntent::Start).as_slice() {
            [LifecycleAction::SpawnGeneration {
                generation_id,
                generation_number,
                ..
            }] => (*generation_id, *generation_number),
            actions => panic!("expected initial spawn action, got {actions:?}"),
        };
        let mut first_tree = ManagedProcessTree::spawn(&holding_process_spec())
            .expect("first real generation should enter a Windows Job");
        assert_ne!(first_tree.pid(), 0);
        assert_eq!(
            supervisor.observe_spawn_succeeded(first_id),
            Some(SupervisorState::Running)
        );

        assert_eq!(
            supervisor.submit(LifecycleIntent::Restart),
            vec![LifecycleAction::StopGeneration {
                generation_id: first_id,
                reason: StopReason::Restart,
            }]
        );
        stop_real_tree(&mut first_tree);
        let (second_id, second_number) =
            match supervisor.observe_generation_stopped(first_id).as_slice() {
                [LifecycleAction::SpawnGeneration {
                    generation_id,
                    generation_number,
                    ..
                }] => (*generation_id, *generation_number),
                actions => panic!("restart should spawn only after old Job cleanup: {actions:?}"),
            };
        assert_eq!(second_number, first_number + 1);
        assert!(!supervisor.accepts_generation_callback(first_id));

        let mut second_tree = ManagedProcessTree::spawn(&holding_process_spec())
            .expect("replacement generation should enter a fresh Windows Job");
        assert_eq!(
            supervisor.observe_spawn_succeeded(second_id),
            Some(SupervisorState::Running)
        );
        assert_eq!(
            supervisor.submit(LifecycleIntent::AppShutdown),
            vec![LifecycleAction::StopGeneration {
                generation_id: second_id,
                reason: StopReason::AppShutdown,
            }]
        );
        stop_real_tree(&mut second_tree);
        assert!(supervisor.observe_generation_stopped(second_id).is_empty());
        assert_eq!(supervisor.snapshot().state, SupervisorState::Stopped);
        assert!(supervisor.snapshot().app_shutdown);
        assert!(supervisor.submit(LifecycleIntent::Restart).is_empty());
    }
}
