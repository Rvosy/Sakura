use serde::Serialize;
use xcap::Monitor;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CapturePrototypeResult {
    width: u32,
    height: u32,
    byte_length: usize,
    monitor_count: usize,
}

#[tauri::command]
pub async fn capture_screen_prototype() -> Result<CapturePrototypeResult, String> {
    tauri::async_runtime::spawn_blocking(capture_primary_monitor)
        .await
        .map_err(|error| error.to_string())?
}

fn capture_primary_monitor() -> Result<CapturePrototypeResult, String> {
    let monitors = Monitor::all().map_err(|error| error.to_string())?;
    let primary_index = select_primary_index(
        &monitors
            .iter()
            .map(|monitor| monitor.is_primary().unwrap_or(false))
            .collect::<Vec<_>>(),
    )
    .ok_or_else(|| "未检测到可截图的显示器".to_string())?;
    let image = monitors[primary_index]
        .capture_image()
        .map_err(|error| error.to_string())?;
    let width = image.width();
    let height = image.height();
    let byte_length = image.into_raw().len();

    Ok(CapturePrototypeResult {
        width,
        height,
        byte_length,
        monitor_count: monitors.len(),
    })
}

fn select_primary_index(primary_flags: &[bool]) -> Option<usize> {
    primary_flags
        .iter()
        .position(|is_primary| *is_primary)
        .or_else(|| (!primary_flags.is_empty()).then_some(0))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selects_primary_monitor_or_first_fallback() {
        assert_eq!(select_primary_index(&[false, true, false]), Some(1));
        assert_eq!(select_primary_index(&[false, false]), Some(0));
        assert_eq!(select_primary_index(&[]), None);
    }
}
