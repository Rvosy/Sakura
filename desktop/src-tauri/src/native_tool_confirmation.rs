//! Application-owned native confirmation prompt; no WebView command is exposed.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::Manager;

use crate::chat_bridge::ToolConfirmationPublication;

const CONFIRMATION_LEASE: Duration = Duration::from_secs(60);

#[derive(Default)]
pub struct NativeToolConfirmationState {
    current: Mutex<Option<PendingPrompt>>,
}

struct PendingPrompt {
    action_id: String,
    cancelled: Arc<AtomicBool>,
}

impl NativeToolConfirmationState {
    pub fn cancel_current(&self) {
        if let Ok(mut current) = self.current.lock() {
            if let Some(prompt) = current.take() {
                prompt.cancelled.store(true, Ordering::Release);
            }
        }
    }

    fn begin(&self, action_id: &str) -> Result<Arc<AtomicBool>, String> {
        let mut current = self
            .current
            .lock()
            .map_err(|_| "TOOL_CONFIRMATION_STATE_UNAVAILABLE".to_string())?;
        if current.is_some() {
            return Err("TOOL_CONFIRMATION_ALREADY_PENDING".to_string());
        }
        let cancelled = Arc::new(AtomicBool::new(false));
        *current = Some(PendingPrompt {
            action_id: action_id.to_string(),
            cancelled: cancelled.clone(),
        });
        Ok(cancelled)
    }

    fn finish(&self, action_id: &str, cancelled: &Arc<AtomicBool>) -> bool {
        let Ok(mut current) = self.current.lock() else {
            return false;
        };
        let matches = current.as_ref().is_some_and(|prompt| {
            prompt.action_id == action_id && Arc::ptr_eq(&prompt.cancelled, cancelled)
        });
        if !matches {
            return false;
        }
        current.take();
        !cancelled.load(Ordering::Acquire)
    }

    #[cfg(test)]
    fn pending_count(&self) -> usize {
        self.current
            .lock()
            .map_or(0, |current| usize::from(current.is_some()))
    }
}

pub fn request(app: &tauri::AppHandle, request: ToolConfirmationPublication) -> Result<(), String> {
    let lease = confirmation_lease(&request.expires_at)?;
    if let Some(window) = app.get_webview_window("main") {
        window
            .show()
            .map_err(|_| "TOOL_CONFIRMATION_FOCUS_FAILED".to_string())?;
        window
            .set_focus()
            .map_err(|_| "TOOL_CONFIRMATION_FOCUS_FAILED".to_string())?;
    }
    let state = app.state::<NativeToolConfirmationState>();
    let cancelled = state.begin(&request.action_id)?;
    let app = app.clone();
    std::thread::Builder::new()
        .name("native-tool-confirmation".to_string())
        .spawn(move || {
            let monitor_done = Arc::new(AtomicBool::new(false));
            let monitor = {
                let app = app.clone();
                let expected_generation = request.generation_id.clone();
                let cancelled = cancelled.clone();
                let done = monitor_done.clone();
                std::thread::Builder::new()
                    .name("native-tool-confirmation-generation".to_string())
                    .spawn(move || {
                        while !done.load(Ordering::Acquire) && !cancelled.load(Ordering::Acquire) {
                            let current = app
                                .state::<crate::ShellLifecycleState>()
                                .handle
                                .as_ref()
                                .and_then(|handle| handle.available_generation_id().ok())
                                .flatten();
                            if current.as_deref() != Some(expected_generation.as_str()) {
                                cancelled.store(true, Ordering::Release);
                                break;
                            }
                            std::thread::sleep(Duration::from_millis(100));
                        }
                    })
                    .ok()
            };
            let confirm = show_blocking(&request, cancelled.clone(), lease);
            monitor_done.store(true, Ordering::Release);
            if let Some(monitor) = monitor {
                let _ = monitor.join();
            }
            let state = app.state::<NativeToolConfirmationState>();
            if !state.finish(&request.action_id, &cancelled) {
                return;
            }
            if let Ok(bridge) = app
                .state::<crate::ShellLifecycleState>()
                .handle
                .as_ref()
                .ok_or(())
                .and_then(|handle| handle.chat_bridge().map_err(|_| ()))
            {
                let _ = bridge.decide_tool_action(&request.action_id, confirm);
            }
        })
        .map_err(|_| {
            state.cancel_current();
            "TOOL_CONFIRMATION_UNAVAILABLE".to_string()
        })?;
    Ok(())
}

fn confirmation_lease(expires_at: &str) -> Result<Duration, String> {
    use time::format_description::well_known::Rfc3339;
    use time::OffsetDateTime;

    let expires_at = OffsetDateTime::parse(expires_at, &Rfc3339)
        .map_err(|_| "TOOL_CONFIRMATION_EXPIRY_INVALID".to_string())?;
    let remaining = expires_at - OffsetDateTime::now_utc();
    let remaining = Duration::try_from(remaining).unwrap_or(Duration::ZERO);
    Ok(remaining.min(CONFIRMATION_LEASE))
}

#[cfg(target_os = "windows")]
fn show_blocking(
    request: &ToolConfirmationPublication,
    cancelled: Arc<AtomicBool>,
    lease: Duration,
) -> bool {
    use windows_sys::Win32::UI::Controls::{
        TaskDialogIndirect, TASKDIALOGCONFIG, TASKDIALOGCONFIG_0, TDCBF_CANCEL_BUTTON,
        TDCBF_OK_BUTTON, TDF_ALLOW_DIALOG_CANCELLATION, TDF_CALLBACK_TIMER, TDF_SIZE_TO_CONTENT,
        TDM_CLICK_BUTTON, TDN_TIMER, TD_INFORMATION_ICON, TD_WARNING_ICON,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{SendMessageW, IDCANCEL, IDOK};

    struct CallbackState {
        cancelled: Arc<AtomicBool>,
        deadline: Instant,
    }

    unsafe extern "system" fn callback(
        hwnd: windows_sys::Win32::Foundation::HWND,
        message: u32,
        _wparam: windows_sys::Win32::Foundation::WPARAM,
        _lparam: windows_sys::Win32::Foundation::LPARAM,
        reference: isize,
    ) -> windows_sys::core::HRESULT {
        if message == TDN_TIMER as u32 {
            let state = unsafe { &*(reference as *const CallbackState) };
            if state.cancelled.load(Ordering::Acquire) || Instant::now() >= state.deadline {
                unsafe {
                    SendMessageW(hwnd, TDM_CLICK_BUTTON as u32, IDCANCEL as usize, 0);
                }
            }
        }
        0
    }

    let title = wide(&request.title);
    let description = wide(&format!(
        "{}\n\n风险：{}",
        request.summary,
        risk_label(&request.risk)
    ));
    let callback_state = Box::new(CallbackState {
        cancelled,
        deadline: Instant::now() + lease,
    });
    let config = TASKDIALOGCONFIG {
        cbSize: std::mem::size_of::<TASKDIALOGCONFIG>() as u32,
        dwFlags: TDF_ALLOW_DIALOG_CANCELLATION | TDF_CALLBACK_TIMER | TDF_SIZE_TO_CONTENT,
        dwCommonButtons: TDCBF_OK_BUTTON | TDCBF_CANCEL_BUTTON,
        pszWindowTitle: title.as_ptr(),
        pszContent: description.as_ptr(),
        Anonymous1: TASKDIALOGCONFIG_0 {
            pszMainIcon: if request.risk == "destructive" {
                TD_WARNING_ICON
            } else {
                TD_INFORMATION_ICON
            },
        },
        pfCallback: Some(callback),
        lpCallbackData: (&*callback_state as *const CallbackState) as isize,
        ..Default::default()
    };
    let mut button = 0;
    let result = unsafe {
        TaskDialogIndirect(
            &config,
            &mut button,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    result == 0 && button == IDOK
}

#[cfg(not(target_os = "windows"))]
fn show_blocking(
    request: &ToolConfirmationPublication,
    cancelled: Arc<AtomicBool>,
    _lease: Duration,
) -> bool {
    use rfd::{MessageButtons, MessageDialog, MessageDialogResult, MessageLevel};

    if cancelled.load(Ordering::Acquire) {
        return false;
    }
    let description = format!("{}\n\n风险：{}", request.summary, risk_label(&request.risk));
    let confirmed = matches!(
        MessageDialog::new()
            .set_level(if request.risk == "destructive" {
                MessageLevel::Warning
            } else {
                MessageLevel::Info
            })
            .set_title(&request.title)
            .set_description(description)
            .set_buttons(MessageButtons::OkCancel)
            .show(),
        MessageDialogResult::Ok | MessageDialogResult::Yes
    );
    confirmed && !cancelled.load(Ordering::Acquire)
}

#[cfg(target_os = "windows")]
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

fn risk_label(risk: &str) -> &'static str {
    if risk == "destructive" {
        "此操作会删除数据且必须确认"
    } else {
        "此操作会修改长期记忆"
    }
}

#[cfg(test)]
mod wp_4_02_tests {
    use std::time::Duration;

    use super::{confirmation_lease, risk_label, NativeToolConfirmationState};

    #[test]
    fn wp_4_02_native_prompt_uses_stable_risk_copy() {
        assert!(risk_label("destructive").contains("删除"));
        assert!(risk_label("write").contains("修改"));
    }

    #[test]
    fn wp_4_02_native_prompt_is_single_and_cancelled_generation_clears_it() {
        let state = NativeToolConfirmationState::default();
        let token = state.begin(&"a".repeat(32)).unwrap();
        assert_eq!(state.pending_count(), 1);
        assert!(state.begin(&"b".repeat(32)).is_err());
        state.cancel_current();
        assert_eq!(state.pending_count(), 0);
        assert!(!state.finish(&"a".repeat(32), &token));
    }

    #[test]
    fn wp_4_02_native_prompt_rejects_invalid_or_expired_leases() {
        assert!(confirmation_lease("not-a-deadline").is_err());
        assert_eq!(
            confirmation_lease("2000-01-01T00:00:00Z").unwrap(),
            Duration::ZERO
        );
    }
}
