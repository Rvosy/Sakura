#[cfg(target_os = "macos")]
use std::sync::{Mutex, OnceLock};

#[derive(Clone, Copy)]
struct SurfaceViewport {
    canvas: [u32; 2],
    offset: [u32; 2],
    scale: f64,
}

impl SurfaceViewport {
    fn frame(self, window_height: f64, flipped: bool) -> [f64; 4] {
        let width = f64::from(self.canvas[0]) * self.scale;
        let height = f64::from(self.canvas[1]) * self.scale;
        let left = -f64::from(self.offset[0]) * self.scale;
        let top = -f64::from(self.offset[1]) * self.scale;
        [
            left,
            if flipped {
                top
            } else {
                window_height - top - height
            },
            width,
            height,
        ]
    }
}

#[cfg(target_os = "macos")]
fn pending() -> &'static Mutex<Option<SurfaceViewport>> {
    static PENDING: OnceLock<Mutex<Option<SurfaceViewport>>> = OnceLock::new();
    PENDING.get_or_init(|| Mutex::new(None))
}

#[cfg(target_os = "macos")]
pub fn prepare(
    application: &crate::window_geometry::LayoutApplication,
    canvas: [u32; 2],
) -> Result<(), String> {
    let scale = application.content_scale;
    if !scale.is_finite() || scale <= 0.0 || canvas.contains(&0) {
        return Err("MACOS_SURFACE_VIEWPORT_INVALID".to_string());
    }
    *pending()
        .lock()
        .map_err(|_| "MACOS_SURFACE_VIEWPORT_UNAVAILABLE")? = Some(SurfaceViewport {
        canvas,
        offset: [application.active_bounds[0], application.active_bounds[1]],
        scale,
    });
    Ok(())
}

#[cfg(target_os = "macos")]
pub fn apply_frame(
    window: &objc2_app_kit::NSWindow,
    frame: objc2_foundation::NSRect,
) -> Result<(), String> {
    use objc2::ClassType;
    use objc2_app_kit::NSAutoresizingMaskOptions;
    use objc2_foundation::{NSObjectProtocol, NSPoint, NSRect, NSSize};
    use objc2_web_kit::WKWebView;

    let viewport = pending()
        .lock()
        .map_err(|_| "MACOS_SURFACE_VIEWPORT_UNAVAILABLE")?
        .ok_or("MACOS_SURFACE_VIEWPORT_NOT_PREPARED")?;
    let host = window
        .contentView()
        .ok_or("MACOS_SURFACE_HOST_UNAVAILABLE")?;
    let webview = host
        .subviews()
        .iter()
        .find(|view| view.isKindOfClass(WKWebView::class()))
        .ok_or("MACOS_SURFACE_WEBVIEW_UNAVAILABLE")?;
    // WebKit otherwise interprets the cropped-off top as a titlebar/content inset, shifting the
    // document and changing innerHeight even though the NSView bounds are fixed. This WebKit SPI
    // predates the public macOS 26 obscuredContentInsets API; guard it before opting into cropping.
    if !webview.respondsToSelector(objc2::sel!(_setAutomaticallyAdjustsContentInsets:)) {
        return Err("MACOS_SURFACE_FIXED_INSETS_UNAVAILABLE".to_string());
    }
    unsafe {
        let _: () = objc2::msg_send![&*webview, _setAutomaticallyAdjustsContentInsets: false];
    }
    // Wry's default width/height autoresizing asks WebKit to rebuild its remote backing whenever
    // NSWindow changes. Keep the canonical canvas fixed, and crop it with the small native window.
    // Disable autoresizing BEFORE changing the window, then move the unchanged child in the same
    // AppKit transaction. No JavaScript stage-offset acknowledgement is needed for a resize.
    webview.setAutoresizingMask(NSAutoresizingMaskOptions::empty());
    window.setFrame_display(frame, false);
    let [x, y, width, height] = viewport.frame(host.bounds().size.height, host.isFlipped());
    let target = NSRect::new(NSPoint::new(x, y), NSSize::new(width, height));
    let previous = webview.frame();
    if previous.size != target.size {
        webview.setFrame(target);
    } else if previous.origin != target.origin {
        webview.setFrameOrigin(target.origin);
    }
    if std::env::var_os("SAKURA_TRACE_MACOS_SURFACE").is_some() {
        eprintln!("[macos-surface-viewport] resized={} canvas=({width:.1},{height:.1}) origin=({x:.1},{y:.1}) crop=({:.1},{:.1})",
            previous.size != target.size, frame.size.width, frame.size.height);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::SurfaceViewport;

    #[test]
    fn window_surface_regression_macos_viewport_keeps_canvas_and_screen_anchor_across_crops() {
        let canvas = crate::composer_resident_viewport(&crate::layout_contract().unwrap());
        for scale in [0.75, 1.0, 1.25] {
            let anchor = [450.0, 1050.0];
            for (offset, size) in [
                ([236, 780], [428, 300]),
                ([20, 200], [860, 1152]),
                ([80, 600], [740, 480]),
                ([210, 1300], [480, canvas[1] - 1300]),
            ] {
                let viewport = SurfaceViewport {
                    canvas,
                    offset,
                    scale,
                };
                let frame = viewport.frame(f64::from(size[1]) * scale, false);
                assert_eq!(
                    [frame[2], frame[3]],
                    [f64::from(canvas[0]) * scale, f64::from(canvas[1]) * scale]
                );
                let local_anchor = [
                    (anchor[0] - f64::from(offset[0])) * scale,
                    (anchor[1] - f64::from(offset[1])) * scale,
                ];
                assert_eq!(frame[0] + anchor[0] * scale, local_anchor[0]);
                assert_eq!(
                    f64::from(size[1]) * scale - frame[1] - frame[3] + anchor[1] * scale,
                    local_anchor[1]
                );
                let flipped = viewport.frame(f64::from(size[1]) * scale, true);
                assert_eq!(flipped[1] + anchor[1] * scale, local_anchor[1]);
            }
        }
    }
}
