mod app_state;
mod audio;
pub mod brain_host;
mod capture;
pub mod ipc;
mod tray;
mod windows;

use tauri::Manager;

pub fn run() {
    let app = tauri::Builder::default()
        .register_uri_scheme_protocol("sakura-asset", app_state::character_asset_protocol)
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            windows::show_main_window(app);
        }))
        .setup(|app| {
            tray::build_tray(app)?;
            app.manage(app_state::DesktopAppState::start(app.handle().clone()));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            app_state::brain_status,
            app_state::pet_bootstrap,
            app_state::chat_send,
            app_state::chat_cancel,
            app_state::chat_confirm_action,
            app_state::chat_reject_action,
            windows::start_dragging,
            windows::set_pet_visible,
            windows::set_click_through,
            windows::set_always_on_top,
            windows::apply_pet_window_layout,
            audio::play_audio_prototype,
            capture::capture_screen_prototype,
        ])
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
