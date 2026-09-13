//! Point-based input routing for animated Windows surfaces. Rendering regions stay unchanged.
#[cfg(windows)]
mod native {
    use std::sync::{Arc, Condvar, Mutex, OnceLock};
    use std::time::{Duration, Instant};
    use tauri::{Emitter, Manager, WebviewWindow};
    use windows::Win32::{
        Foundation::{HWND, POINT},
        Graphics::Gdi::ScreenToClient,
        UI::{
            Input::KeyboardAndMouse::{GetAsyncKeyState, VK_LBUTTON, VK_MBUTTON, VK_RBUTTON},
            WindowsAndMessaging::{GetCursorPos, IsWindow, IsWindowVisible},
        },
    };

    #[derive(Clone, serde::Serialize)]
    #[serde(rename_all = "camelCase")]
    pub struct Query {
        session: String,
        id: u64,
        point: [f64; 2],
    }
    struct Pending {
        query: Query,
        cursor: [i32; 2],
        origin: [i32; 2],
        rect: crate::window_interaction::PhysicalHitRect,
        layout_revision: u64,
        portrait_revision: u64,
        dpi: f64,
        started: Instant,
    }
    #[derive(Clone, Default, serde::Serialize)]
    #[serde(rename_all = "camelCase")]
    pub struct Metrics {
        pub session: String,
        ui_ticks: u64,
        queries: u64,
        accepted: u64,
        rejected: u64,
        switches: u64,
        total_reply_ms: f64,
        max_reply_ms: f64,
    }
    struct Active {
        window: WebviewWindow,
        hwnd: isize,
        session: String,
        ignored: bool,
        pending: Option<Pending>,
        metrics: Metrics,
    }
    #[derive(Default)]
    struct Router {
        active: Option<Active>,
        next_id: u64,
    }
    type Shared = Arc<(Mutex<Router>, Condvar)>;
    fn shared() -> &'static Shared {
        static SHARED: OnceLock<Shared> = OnceLock::new();
        SHARED.get_or_init(|| Arc::new((Mutex::new(Router::default()), Condvar::new())))
    }
    fn pressed() -> bool {
        [VK_LBUTTON, VK_RBUTTON, VK_MBUTTON]
            .iter()
            .any(|key| unsafe { GetAsyncKeyState(i32::from(key.0)) } < 0)
    }
    fn cursor(hwnd: HWND) -> Option<([i32; 2], [i32; 2])> {
        let mut point = POINT::default();
        unsafe {
            GetCursorPos(&mut point).ok()?;
        }
        let screen = [point.x, point.y];
        if !unsafe { ScreenToClient(hwnd, &mut point) }.as_bool() {
            return None;
        }
        Some((
            [point.x, point.y],
            [screen[0] - point.x, screen[1] - point.y],
        ))
    }
    fn needs_ui_tick(active: &mut Active) -> bool {
        let hwnd = HWND(active.hwnd as *mut _);
        if pressed() || !unsafe { IsWindowVisible(hwnd) }.as_bool() {
            return false;
        }
        let Some((point, _)) = cursor(hwnd) else {
            return false;
        };
        let state = active.window.state::<Mutex<crate::WindowGeometrySession>>();
        let Ok(geometry) = state.try_lock() else {
            return false;
        };
        let Some(model) = geometry.hit_regions.as_ref() else {
            return false;
        };
        let control = geometry.context_menu_open
            || model
                .interactive
                .iter()
                .chain(&model.neutral)
                .chain(&model.extra_native_rectangles)
                .chain(model.drag.iter().skip(1))
                .any(|rect| rect.contains(point));
        if control {
            return active.ignored;
        }
        if model.drag.first().is_some_and(|rect| rect.contains(point)) {
            return query_work(
                active.pending.as_ref().map(|p| p.started.elapsed()),
                active.ignored,
            ) != QueryWork::Wait;
        }
        !active.ignored
    }
    fn route(active: &mut Active, ignored: bool) {
        if active.ignored != ignored && active.window.set_ignore_cursor_events(ignored).is_ok() {
            active.ignored = ignored;
            active.metrics.switches += 1;
        }
    }
    #[derive(Debug, PartialEq, Eq)]
    enum QueryWork {
        Wait,
        RestoreRectangle,
        Request,
    }
    fn query_work(age: Option<Duration>, ignored: bool) -> QueryWork {
        match age {
            None => QueryWork::Request,
            Some(age) if age >= Duration::from_millis(150) && ignored => {
                QueryWork::RestoreRectangle
            }
            Some(_) => QueryWork::Wait,
        }
    }
    fn tick(router: &mut Router) {
        let Some(active) = router.active.as_mut() else {
            return;
        };
        active.metrics.ui_ticks += 1;
        if active.window.hwnd().is_err() {
            router.active = None;
            return;
        }
        // Never take ownership from an underlying application's drag, or release our own.
        if pressed() {
            return;
        }
        if !active.window.is_visible().unwrap_or(false) {
            return;
        }
        let Some((point, origin)) = cursor(HWND(active.hwnd as *mut _)) else {
            return;
        };
        let window = active.window.clone();
        let state = window.state::<Mutex<crate::WindowGeometrySession>>();
        let Ok(geometry) = state.try_lock() else {
            return;
        };
        let Some(model) = geometry.hit_regions.as_ref() else {
            return;
        };
        let control = geometry.context_menu_open
            || model
                .interactive
                .iter()
                .chain(&model.neutral)
                .chain(&model.extra_native_rectangles)
                .chain(model.drag.iter().skip(1))
                .any(|rect| rect.contains(point));
        if control {
            route(active, false);
            return;
        }
        let Some(rect) = model
            .drag
            .first()
            .copied()
            .filter(|rect| rect.contains(point))
        else {
            route(active, true);
            return;
        };
        match query_work(
            active.pending.as_ref().map(|p| p.started.elapsed()),
            active.ignored,
        ) {
            QueryWork::Wait => return,
            QueryWork::RestoreRectangle => {
                // Retain the request until it returns or the binding retires.
                // Timeout retries would queue events in a stalled WebView.
                route(active, false);
                return;
            }
            QueryWork::Request => {}
        }
        router.next_id += 1;
        active.metrics.queries += 1;
        let dpi = window.scale_factor().unwrap_or(1.0);
        let query = Query {
            session: active.session.clone(),
            id: router.next_id,
            point: [f64::from(point[0]) / dpi, f64::from(point[1]) / dpi],
        };
        active.pending = Some(Pending {
            query: query.clone(),
            cursor: point,
            origin,
            rect,
            layout_revision: geometry.applied_revision,
            portrait_revision: geometry.portrait_hit_revision,
            dpi,
            started: Instant::now(),
        });
        if window.emit("visual-hit-test", query).is_err() {
            active.pending = None;
            route(active, false);
        }
    }
    fn start() -> Result<(), String> {
        static STARTED: OnceLock<Result<(), String>> = OnceLock::new();
        STARTED
            .get_or_init(|| {
                let shared = shared().clone();
                std::thread::Builder::new()
                    .name("pet-dynamic-hit-test".into())
                    .spawn(move || {
                        let pending = Arc::new(std::sync::atomic::AtomicBool::new(false));
                        loop {
                            let (lock, wake) = &*shared;
                            let mut router = lock.lock().unwrap_or_else(|e| e.into_inner());
                            while router.active.is_none() {
                                router = wake.wait(router).unwrap_or_else(|e| e.into_inner());
                            }
                            let active = router.active.as_mut().unwrap();
                            if !unsafe { IsWindow(Some(HWND(active.hwnd as *mut _))) }.as_bool() {
                                router.active = None;
                                continue;
                            }
                            let needs_tick = needs_ui_tick(active);
                            let window = router.active.as_ref().unwrap().window.clone();
                            drop(router);
                            if needs_tick
                                && !pending.swap(true, std::sync::atomic::Ordering::AcqRel)
                            {
                                let queued = pending.clone();
                                let state = shared.clone();
                                if window
                                    .run_on_main_thread(move || {
                                        let mut router =
                                            state.0.lock().unwrap_or_else(|e| e.into_inner());
                                        tick(&mut router);
                                        queued.store(false, std::sync::atomic::Ordering::Release);
                                    })
                                    .is_err()
                                {
                                    pending.store(false, std::sync::atomic::Ordering::Release);
                                    shared.0.lock().unwrap_or_else(|e| e.into_inner()).active =
                                        None;
                                }
                            }
                            std::thread::sleep(Duration::from_millis(17));
                        }
                    })
                    .map(|_| ())
                    .map_err(|e| e.to_string())
            })
            .clone()
    }
    pub fn configure(
        window: WebviewWindow,
        session: String,
        enabled: bool,
    ) -> Result<bool, String> {
        start()?;
        let hwnd = window.hwnd().map_err(|e| e.to_string())?.0 as isize;
        let mut router = shared().0.lock().map_err(|e| e.to_string())?;
        if !enabled && router.active.as_ref().is_none_or(|a| a.session != session) {
            return Ok(false);
        }
        if let Some(active) = router.active.as_mut() {
            route(active, false);
        }
        router.active = enabled.then(|| Active {
            window,
            hwnd,
            metrics: Metrics {
                session: session.clone(),
                ..Metrics::default()
            },
            session,
            ignored: false,
            pending: None,
        });
        shared().1.notify_one();
        Ok(true)
    }
    pub fn submit(
        window: WebviewWindow,
        session: String,
        id: u64,
        hit: bool,
    ) -> Result<(), String> {
        let mut router = shared().0.lock().map_err(|e| e.to_string())?;
        let Some(active) = router.active.as_mut().filter(|a| a.session == session) else {
            return Ok(());
        };
        if active.pending.as_ref().is_none_or(|p| p.query.id != id) {
            return Ok(());
        }
        let pending = active.pending.take().unwrap();
        if pressed() || pending.started.elapsed() > Duration::from_millis(150) {
            return Ok(());
        }
        let Some((point, origin)) = cursor(HWND(active.hwnd as *mut _)) else {
            return Ok(());
        };
        let state = window.state::<Mutex<crate::WindowGeometrySession>>();
        let geometry = state.lock().map_err(|e| e.to_string())?;
        let valid = geometry.hit_regions.as_ref().is_some_and(|model| {
            model.drag.first() == Some(&pending.rect)
                && !geometry.context_menu_open
                && geometry.applied_revision == pending.layout_revision
                && geometry.portrait_hit_revision == pending.portrait_revision
                && window.scale_factor().ok() == Some(pending.dpi)
                && !model
                    .interactive
                    .iter()
                    .chain(&model.neutral)
                    .chain(&model.extra_native_rectangles)
                    .chain(model.drag.iter().skip(1))
                    .any(|rect| rect.contains(point))
        });
        if valid && point == pending.cursor && origin == pending.origin {
            active.metrics.accepted += 1;
            let elapsed = pending.started.elapsed().as_secs_f64() * 1000.0;
            active.metrics.total_reply_ms += elapsed;
            active.metrics.max_reply_ms = active.metrics.max_reply_ms.max(elapsed);
            route(active, !hit);
        } else {
            active.metrics.rejected += 1;
        }
        Ok(())
    }
    pub fn metrics() -> Option<Metrics> {
        shared()
            .0
            .lock()
            .ok()?
            .active
            .as_ref()
            .map(|active| active.metrics.clone())
    }
    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn stalled_renderer_restores_input_once_without_queuing_more_requests() {
            assert_eq!(query_work(None, true), QueryWork::Request);
            assert_eq!(
                query_work(Some(Duration::from_millis(149)), true),
                QueryWork::Wait
            );
            for age in [Duration::from_millis(150), Duration::from_secs(60)] {
                assert_eq!(query_work(Some(age), true), QueryWork::RestoreRectangle);
                assert_eq!(query_work(Some(age), false), QueryWork::Wait);
            }
            // A late result is retired by submit; only then can another query be issued.
            assert_eq!(query_work(None, false), QueryWork::Request);
        }
    }
}

#[tauri::command]
pub fn configure_dynamic_hit_test(
    window: tauri::WebviewWindow,
    session: String,
    enabled: bool,
) -> Result<bool, String> {
    if window.label() != "main" || session.is_empty() || session.len() > 128 {
        return Err("DYNAMIC_HIT_TEST_INVALID".into());
    }
    #[cfg(windows)]
    {
        native::configure(window, session, enabled)
    }
    #[cfg(not(windows))]
    {
        let _ = enabled;
        Ok(false)
    }
}

#[tauri::command]
pub fn submit_dynamic_hit_test(
    window: tauri::WebviewWindow,
    session: String,
    id: u64,
    hit: bool,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".into());
    }
    #[cfg(windows)]
    {
        native::submit(window, session, id, hit)
    }
    #[cfg(not(windows))]
    {
        let _ = (session, id, hit);
        Ok(())
    }
}

#[tauri::command]
pub fn dynamic_hit_test_status(window: tauri::WebviewWindow) -> Result<serde_json::Value, String> {
    if window.label() != "main" {
        return Err("PET_WINDOW_REQUIRED".into());
    }
    #[cfg(windows)]
    {
        serde_json::to_value(native::metrics()).map_err(|e| e.to_string())
    }
    #[cfg(not(windows))]
    {
        Ok(serde_json::Value::Null)
    }
}
