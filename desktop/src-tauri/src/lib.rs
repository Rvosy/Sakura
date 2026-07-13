mod audio;
mod capture;
mod tray;
mod windows;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            windows::show_main_window(app);
        }))
        .setup(|app| {
            tray::build_tray(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            windows::start_dragging,
            windows::set_pet_visible,
            windows::set_click_through,
            windows::set_always_on_top,
            audio::play_audio_prototype,
            capture::capture_screen_prototype,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Sakura desktop");
}
