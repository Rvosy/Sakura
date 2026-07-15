#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

const STARTUP_HTML: &str = include_str!("../../frontend/index.html");
const STARTUP_STYLES: &str = include_str!("../../frontend/styles.css");

fn main() {
    let _embedded_startup_asset_lengths = (STARTUP_HTML.len(), STARTUP_STYLES.len());

    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("failed to run Sakura Runtime v2 Shell");
}

#[cfg(test)]
mod tests {
    use super::{STARTUP_HTML, STARTUP_STYLES};

    #[test]
    fn startup_page_has_runtime_and_loaded_markers() {
        assert!(STARTUP_HTML.contains("Sakura Runtime v2"));
        assert!(STARTUP_HTML.contains("Startup"));
        assert!(STARTUP_HTML.contains("data-shell-state=\"startup-loaded\""));
    }

    #[test]
    fn startup_page_is_static_and_local() {
        let html = STARTUP_HTML.to_ascii_lowercase();
        let css = STARTUP_STYLES.to_ascii_lowercase();

        assert!(!html.contains("<script"));
        assert!(!html.contains("http://"));
        assert!(!html.contains("https://"));
        assert!(!css.contains("url("));
        assert!(!css.contains("@import"));
    }
}
