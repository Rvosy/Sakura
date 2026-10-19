use tauri::WebviewWindow;

#[tauri::command]
pub(crate) async fn data_transfer_choose(
    window: WebviewWindow,
    export: bool,
    format: String,
) -> Result<Option<String>, String> {
    if !matches!(window.label(), "history" | "settings") {
        return Err("DATA_TRANSFER_WINDOW_REQUIRED".into());
    }
    let extension = if format == "txt" { "txt" } else { "json" };
    let dialog = rfd::AsyncFileDialog::new().add_filter(
        if extension == "txt" {
            "纯文本"
        } else {
            "Sakura 数据文件"
        },
        &[extension],
    );
    let selected = if export {
        dialog
            .set_file_name(if extension == "txt" {
                "sakura.txt"
            } else {
                "sakura-data.json"
            })
            .save_file()
            .await
    } else {
        dialog.pick_file().await
    };
    Ok(selected.map(|file| file.path().to_string_lossy().into_owned()))
}
