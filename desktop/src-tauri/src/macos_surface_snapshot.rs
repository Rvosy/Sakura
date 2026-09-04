use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use block2::RcBlock;
use objc2::{rc::Retained, MainThreadMarker};
use objc2_app_kit::{
    NSAutoresizingMaskOptions, NSImage, NSImageView, NSView, NSWindow, NSWindowOrderingMode,
};
use objc2_foundation::{NSError, NSRect};
use objc2_web_kit::{WKSnapshotConfiguration, WKWebView};
use tokio::sync::oneshot;

const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone, Copy)]
struct SurfaceSnapshot {
    revision: u64,
    view: usize,
    source_window_frame: [f64; 4],
}

#[derive(Default)]
struct SurfaceSnapshotState {
    requested_revision: Option<u64>,
    overlay: Option<SurfaceSnapshot>,
}

impl SurfaceSnapshotState {
    fn begin_request(&mut self, revision: u64) -> Option<SurfaceSnapshot> {
        self.requested_revision = Some(revision);
        self.overlay.take()
    }

    fn accepts(&self, revision: u64) -> bool {
        self.requested_revision == Some(revision)
    }

    fn take_revision(&mut self, revision: u64) -> Option<SurfaceSnapshot> {
        if self.accepts(revision) {
            self.requested_revision = None;
        }
        if self
            .overlay
            .is_some_and(|overlay| overlay.revision == revision)
        {
            self.overlay.take()
        } else {
            None
        }
    }

    fn clear(&mut self) -> Option<SurfaceSnapshot> {
        self.requested_revision = None;
        self.overlay.take()
    }
}

fn snapshot_state() -> &'static Mutex<SurfaceSnapshotState> {
    static STATE: OnceLock<Mutex<SurfaceSnapshotState>> = OnceLock::new();
    STATE.get_or_init(|| Mutex::new(SurfaceSnapshotState::default()))
}

fn trace(phase: &str, detail: impl std::fmt::Display) {
    if std::env::var_os("SAKURA_TRACE_MACOS_SURFACE").is_some() {
        eprintln!("[macos-surface-snapshot] phase={phase} {detail}");
    }
}

unsafe fn view_from_handle<'a>(handle: usize) -> &'a NSView {
    unsafe { &*(handle as *mut NSView) }
}

fn remove_overlay(snapshot: Option<SurfaceSnapshot>) {
    if let Some(snapshot) = snapshot {
        unsafe { view_from_handle(snapshot.view) }.removeFromSuperview();
    }
}

fn complete(
    sender: &Arc<Mutex<Option<oneshot::Sender<Result<bool, String>>>>>,
    result: Result<bool, String>,
) {
    if let Ok(mut sender) = sender.lock() {
        if let Some(sender) = sender.take() {
            let _ = sender.send(result);
        }
    }
}

fn clear_request(revision: u64) {
    if let Ok(mut state) = snapshot_state().lock() {
        if state.accepts(revision) {
            state.requested_revision = None;
        }
    }
}

pub async fn install(window: &tauri::WebviewWindow, revision: u64) -> Result<bool, String> {
    let (sender, receiver) = oneshot::channel();
    let sender = Arc::new(Mutex::new(Some(sender)));
    let callback_sender = sender.clone();
    if let Err(error) = window.with_webview(move |webview| {
        let result = (|| {
            let mtm = MainThreadMarker::new()
                .ok_or_else(|| "MACOS_SURFACE_SNAPSHOT_MAIN_THREAD_REQUIRED".to_string())?;
            let previous = {
                let mut state = snapshot_state()
                    .lock()
                    .map_err(|_| "MACOS_SURFACE_SNAPSHOT_STATE_UNAVAILABLE".to_string())?;
                state.begin_request(revision)
            };
            remove_overlay(previous);

            let native_webview = unsafe {
                Retained::retain(webview.inner().cast::<WKWebView>())
                    .ok_or_else(|| "MACOS_SURFACE_SNAPSHOT_WEBVIEW_UNAVAILABLE".to_string())?
            };
            let host = unsafe { native_webview.superview() }
                .ok_or_else(|| "MACOS_SURFACE_SNAPSHOT_HOST_UNAVAILABLE".to_string())?;
            let ns_window = unsafe { &*webview.ns_window().cast::<NSWindow>() };
            let source_frame = ns_window.frame();
            let configuration = unsafe { WKSnapshotConfiguration::new(mtm) };
            unsafe {
                configuration.setRect(native_webview.bounds());
                configuration.setAfterScreenUpdates(false);
            }

            let completion_webview = native_webview.clone();
            let completion_host = host.clone();
            let completion_sender = callback_sender.clone();
            let completion = RcBlock::new(move |image: *mut NSImage, error: *mut NSError| {
                let result = (|| {
                    if image.is_null() {
                        clear_request(revision);
                        return Err(if error.is_null() {
                            "MACOS_SURFACE_SNAPSHOT_EMPTY".to_string()
                        } else {
                            "MACOS_SURFACE_SNAPSHOT_CAPTURE_FAILED".to_string()
                        });
                    }
                    let image = unsafe { Retained::retain(image) }
                        .ok_or_else(|| "MACOS_SURFACE_SNAPSHOT_EMPTY".to_string())?;
                    let snapshot = NSImageView::imageViewWithImage(&image, mtm);
                    snapshot.setFrame(completion_webview.frame());
                    snapshot.setAutoresizingMask(NSAutoresizingMaskOptions::empty());

                    let mut state = snapshot_state()
                        .lock()
                        .map_err(|_| "MACOS_SURFACE_SNAPSHOT_STATE_UNAVAILABLE".to_string())?;
                    if !state.accepts(revision) {
                        return Ok(false);
                    }
                    completion_host.addSubview_positioned_relativeTo(
                        &snapshot,
                        NSWindowOrderingMode::Above,
                        Some(&completion_webview),
                    );
                    let handle = Retained::as_ptr(&snapshot) as *mut NSView as usize;
                    state.overlay = Some(SurfaceSnapshot {
                        revision,
                        view: handle,
                        source_window_frame: [
                            source_frame.origin.x,
                            source_frame.origin.y,
                            source_frame.size.width,
                            source_frame.size.height,
                        ],
                    });
                    trace(
                        "installed",
                        format_args!(
                            "revision={revision} window=({:.1},{:.1},{:.1},{:.1}) snapshot=({:.1},{:.1},{:.1},{:.1})",
                            source_frame.origin.x,
                            source_frame.origin.y,
                            source_frame.size.width,
                            source_frame.size.height,
                            snapshot.frame().origin.x,
                            snapshot.frame().origin.y,
                            snapshot.frame().size.width,
                            snapshot.frame().size.height,
                        ),
                    );
                    Ok(true)
                })();
                if result.is_err() {
                    clear_request(revision);
                }
                complete(&completion_sender, result);
            });
            unsafe {
                native_webview.takeSnapshotWithConfiguration_completionHandler(
                    Some(&configuration),
                    &completion,
                );
            }
            Ok(())
        })();
        if let Err(error) = result {
            clear_request(revision);
            complete(&callback_sender, Err(error));
        }
    }) {
        clear_request(revision);
        return Err(format!("MACOS_SURFACE_SNAPSHOT_DISPATCH_FAILED:{error}"));
    }

    match tokio::time::timeout(SNAPSHOT_TIMEOUT, receiver).await {
        Ok(Ok(result)) => result,
        Ok(Err(_)) => {
            clear_request(revision);
            Err("MACOS_SURFACE_SNAPSHOT_CANCELLED".to_string())
        }
        Err(_) => {
            clear_request(revision);
            Err("MACOS_SURFACE_SNAPSHOT_TIMEOUT".to_string())
        }
    }
}

pub fn reposition_for_window_frame(frame: NSRect) -> Result<(), String> {
    let snapshot = snapshot_state()
        .lock()
        .map_err(|_| "MACOS_SURFACE_SNAPSHOT_STATE_UNAVAILABLE".to_string())?
        .overlay;
    let Some(snapshot) = snapshot else {
        return Ok(());
    };
    let view = unsafe { view_from_handle(snapshot.view) };
    let mut snapshot_frame = view.frame();
    snapshot_frame.origin.x = snapshot.source_window_frame[0] - frame.origin.x;
    snapshot_frame.origin.y = snapshot.source_window_frame[1] - frame.origin.y;
    view.setFrame(snapshot_frame);
    trace(
        "repositioned",
        format_args!(
            "revision={} window=({:.1},{:.1},{:.1},{:.1}) snapshot=({:.1},{:.1},{:.1},{:.1})",
            snapshot.revision,
            frame.origin.x,
            frame.origin.y,
            frame.size.width,
            frame.size.height,
            snapshot_frame.origin.x,
            snapshot_frame.origin.y,
            snapshot_frame.size.width,
            snapshot_frame.size.height,
        ),
    );
    Ok(())
}

pub async fn finish(window: &tauri::WebviewWindow, revision: u64) -> Result<(), String> {
    let (sender, receiver) = oneshot::channel();
    window
        .with_webview(move |_| {
            let result: Result<(), String> = (|| {
                let overlay = {
                    let mut state = snapshot_state()
                        .lock()
                        .map_err(|_| "MACOS_SURFACE_SNAPSHOT_STATE_UNAVAILABLE".to_string())?;
                    state.take_revision(revision)
                };
                remove_overlay(overlay);
                Ok(())
            })();
            let _ = sender.send(result);
        })
        .map_err(|error| format!("MACOS_SURFACE_SNAPSHOT_DISPATCH_FAILED:{error}"))?;
    tokio::time::timeout(SNAPSHOT_TIMEOUT, receiver)
        .await
        .map_err(|_| "MACOS_SURFACE_SNAPSHOT_TIMEOUT".to_string())?
        .map_err(|_| "MACOS_SURFACE_SNAPSHOT_CANCELLED".to_string())??;
    trace("finished", format_args!("revision={revision}"));
    Ok(())
}

pub fn clear(window: &tauri::WebviewWindow) -> Result<(), String> {
    window
        .with_webview(move |_| {
            let overlay = snapshot_state()
                .lock()
                .ok()
                .and_then(|mut state| state.clear());
            remove_overlay(overlay);
            trace("cleared", "all");
        })
        .map_err(|error| format!("MACOS_SURFACE_SNAPSHOT_DISPATCH_FAILED:{error}"))
}

#[cfg(test)]
mod tests {
    use super::{SurfaceSnapshot, SurfaceSnapshotState};

    fn snapshot(revision: u64) -> SurfaceSnapshot {
        SurfaceSnapshot {
            revision,
            view: revision as usize,
            source_window_frame: [0.0; 4],
        }
    }

    #[test]
    fn stale_completion_and_cleanup_cannot_remove_a_newer_snapshot() {
        let mut state = SurfaceSnapshotState::default();
        assert!(state.begin_request(10).is_none());
        assert!(state.accepts(10));

        state.overlay = Some(snapshot(10));
        assert_eq!(state.begin_request(11).unwrap().revision, 10);
        state.overlay = Some(snapshot(11));

        assert!(!state.accepts(10));
        assert!(state.take_revision(10).is_none());
        assert_eq!(state.overlay.unwrap().revision, 11);
        assert_eq!(state.take_revision(11).unwrap().revision, 11);
        assert!(state.overlay.is_none());
        assert!(state.requested_revision.is_none());
    }
}
