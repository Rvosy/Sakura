mod app_state;
mod audio;
pub mod brain_host;
mod capture;
pub mod ipc;
mod menu_actions;
mod tray;
mod windows;

use serde_json::json;
use tauri::{Emitter, Manager, WindowEvent};

pub fn run() {
    let app = tauri::Builder::default()
        .register_uri_scheme_protocol("sakura-asset", app_state::character_asset_protocol)
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            app_state::show_application_window(app);
        }))
        .setup(|app| {
            tray::build_tray(app)?;
            let state = app_state::DesktopAppState::start(app.handle().clone())
                .map_err(std::io::Error::other)?;
            app.manage(state);
            app.state::<app_state::DesktopAppState>()
                .begin_startup_routing(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            app_state::brain_status,
            app_state::bootstrap_status,
            app_state::pet_bootstrap,
            app_state::set_pet_subtitle_language,
            app_state::set_pet_free_access,
            app_state::set_pet_always_on_top,
            app_state::chat_send,
            app_state::chat_cancel,
            app_state::chat_confirm_action,
            app_state::chat_reject_action,
            app_state::tts_synthesize,
            app_state::tts_cancel,
            app_state::play_tts_audio,
            app_state::stop_tts_audio,
            app_state::set_tts_volume,
            app_state::load_request,
            app_state::host_call,
            app_state::save_settings,
            app_state::apply_settings,
            app_state::begin_layout_preview,
            app_state::preview_layout,
            app_state::cancel_settings,
            app_state::show_studio,
            app_state::close_studio,
            windows::start_dragging,
            windows::set_pet_visible,
            windows::set_click_through,
            windows::apply_pet_window_layout,
            windows::open_settings_window,
            windows::open_studio_window,
            windows::open_history_window,
            windows::open_diagnostics_window,
            audio::play_audio_prototype,
            capture::list_capture_monitors,
            capture::open_capture_overlay,
            capture::capture_selected_region,
            capture::cancel_capture_overlay,
            capture::capture_screen_prototype,
            menu_actions::pet_menu_action,
        ])
        .on_window_event(|window, event| {
            let close_event = match window.label() {
                "settings" => Some("sakura://settings-close-requested"),
                "studio" => Some("sakura://studio-close-requested"),
                _ => None,
            };
            if let (Some(event_name), WindowEvent::CloseRequested { api, .. }) =
                (close_event, event)
            {
                api.prevent_close();
                let _ = window.emit(event_name, json!({}));
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Sakura desktop");
    app.run(|app, event| {
        if matches!(
            event,
            tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
        ) {
            if let Some(state) = app.try_state::<app_state::DesktopAppState>() {
                state.shutdown();
            }
        }
    });
}
