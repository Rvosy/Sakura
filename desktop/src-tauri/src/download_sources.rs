//! Ordered download mirrors shared by the marketplace and signed app updates.
use crate::{product_shell, ui_config::UiConfigRepository};
use reqwest::Url;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{error::Error, sync::LazyLock, time::Duration};
use tauri::{State, WebviewWindow};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DownloadSource {
    pub name: String,
    pub prefix: String,
    pub enabled: bool,
}

pub struct DownloadSources(pub UiConfigRepository);

pub fn defaults() -> Vec<DownloadSource> {
    vec![
        DownloadSource {
            name: "gitproxy.mrhjx.cn".into(),
            prefix: "https://gitproxy.mrhjx.cn/".into(),
            enabled: true,
        },
        DownloadSource {
            name: "ghproxy.vip".into(),
            prefix: "https://ghproxy.vip/".into(),
            enabled: true,
        },
        DownloadSource {
            name: "GitHub 官方".into(),
            prefix: String::new(),
            enabled: true,
        },
    ]
}

pub fn validate(sources: &[DownloadSource]) -> Result<(), String> {
    if !sources.iter().any(|source| source.enabled) {
        return Err("至少启用一个下载源。".into());
    }
    for source in sources {
        if source.name.trim().is_empty() {
            return Err("请填写下载源名称。".into());
        }
        if !source.prefix.is_empty() {
            https_url(&source.prefix)?;
        }
    }
    Ok(())
}

pub fn https_url(value: &str) -> Result<Url, String> {
    let url = Url::parse(value).map_err(|source_error| {
        crate::runtime_log::diagnostic_error("下载地址无效。", source_error)
    })?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err("下载地址必须使用 HTTPS，且不能包含凭据。".into());
    }
    Ok(url)
}

impl DownloadSources {
    pub fn load(&self) -> Result<Vec<DownloadSource>, String> {
        let document = self.0.load("DOWNLOAD")?;
        let sources = match document.pointer("/settings/download_sources") {
            Some(value) => serde_json::from_value(value.clone()).map_err(|source_error| {
                crate::runtime_log::diagnostic_error("下载源配置无效。", source_error)
            })?,
            None => defaults(),
        };
        validate(&sources)?;
        Ok(sources)
    }
}

pub fn candidates(url: &Url, sources: &[DownloadSource]) -> Vec<(String, Url)> {
    if !matches!(
        url.host_str(),
        Some("github.com" | "raw.githubusercontent.com" | "codeload.github.com")
    ) {
        return vec![(url.host_str().unwrap_or("下载站").into(), url.clone())];
    }
    sources
        .iter()
        .filter(|s| s.enabled)
        .map(|s| {
            let resolved = if s.prefix.is_empty() {
                url.clone()
            } else {
                Url::parse(&format!("{}/{}", s.prefix.trim_end_matches('/'), url))
                    .expect("validated source prefix")
            };
            (s.name.clone(), resolved)
        })
        .collect()
}

// A failed network request moves to the next source once. Invalid content is an
// error at the consumer, not a reason to repeatedly download the same artifact.
pub async fn fetch(
    url: &Url,
    sources: &[DownloadSource],
    maximum: usize,
    progress: impl Fn(&str, usize, Option<u64>),
) -> Result<Vec<u8>, String> {
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .read_timeout(Duration::from_secs(20))
        .timeout(Duration::from_secs(600))
        .user_agent("Sakura")
        .build()
        .map_err(|e| e.to_string())?;
    let mut failures = Vec::new();
    for (name, address) in candidates(url, sources) {
        progress(&name, 0, None);
        let attempt = async {
            let mut response = client.get(address).send().await?.error_for_status()?;
            let length = response.content_length();
            let mut data = Vec::new();
            while let Some(chunk) = response.chunk().await? {
                if data.len() + chunk.len() > maximum {
                    return Ok::<_, reqwest::Error>(None);
                }
                data.extend_from_slice(&chunk);
                progress(&name, data.len(), length);
            }
            Ok(Some(data))
        }
        .await;
        match attempt {
            Ok(Some(data)) => return Ok(data),
            Ok(None) => return Err("下载内容超过大小限制。".into()),
            Err(error) => failures.push(format!("{name}（{}）", download_error_detail(error))),
        }
    }
    Err(format!("无法下载，已尝试：{}。", failures.join("；")))
}

fn download_error_detail(error: reqwest::Error) -> String {
    let error = error.without_url();
    let mut chain = vec![error.to_string()];
    let mut source = error.source();
    while let Some(cause) = source {
        chain.push(cause.to_string());
        source = cause.source();
    }
    // Redirects and nested transport errors can contain signed URLs. Keep the
    // actual failure, but omit request URLs regardless of their query key names.
    static URL: LazyLock<regex::Regex> =
        LazyLock::new(|| regex::Regex::new(r#"https?://[^\s<>\"']+"#).unwrap());
    let detail = chain.join("\nCaused by: ");
    crate::runtime_log::sanitize_diagnostic(&URL.replace_all(&detail, "[URL]"), &[], 4096)
}

#[tauri::command]
pub(crate) fn settings_download_sources_get(
    window: WebviewWindow,
    sources: State<'_, DownloadSources>,
) -> Result<Vec<DownloadSource>, String> {
    product_shell::validate_settings_window(&window)?;
    sources.load()
}

#[tauri::command]
pub(crate) fn settings_download_sources_save(
    window: WebviewWindow,
    sources: State<'_, DownloadSources>,
    value: Vec<DownloadSource>,
) -> Result<(), String> {
    product_shell::validate_settings_window(&window)?;
    validate(&value)?;
    sources.0.update("DOWNLOAD", |document| {
        let settings = document
            .get_mut("settings")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| "下载源配置无效。".to_string())?;
        settings.insert("download_sources".into(), json!(value));
        Ok(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::{Arc, Mutex},
        thread,
    };

    fn server(reply: &'static [u8]) -> (String, thread::JoinHandle<()>) {
        let socket = TcpListener::bind("127.0.0.1:0").unwrap();
        let prefix = format!("http://{}/", socket.local_addr().unwrap());
        let task = thread::spawn(move || {
            let (mut connection, _) = socket.accept().unwrap();
            let mut request = [0; 4096];
            let _ = connection.read(&mut request).unwrap();
            connection.write_all(reply).unwrap();
        });
        (prefix, task)
    }

    #[tokio::test]
    async fn a_network_failure_advances_once_and_reports_the_successful_source() {
        let (bad, first) = server(b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n");
        let (good, second) = server(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nZIP!");
        let sources = vec![
            DownloadSource {
                name: "first".into(),
                prefix: bad,
                enabled: true,
            },
            DownloadSource {
                name: "second".into(),
                prefix: good,
                enabled: true,
            },
        ];
        let events = Arc::new(Mutex::new(Vec::new()));
        let result = fetch(
            &Url::parse("https://github.com/example/repo/package.zip").unwrap(),
            &sources,
            10,
            |name, size, _| events.lock().unwrap().push((name.to_string(), size)),
        )
        .await
        .unwrap();
        assert_eq!(result, b"ZIP!");
        let observed = events.lock().unwrap();
        assert_eq!(
            observed
                .iter()
                .filter(|(_, size)| *size == 0)
                .map(|(name, _)| name.as_str())
                .collect::<Vec<_>>(),
            vec!["first", "second"]
        );
        assert_eq!(observed.last(), Some(&("second".into(), 4)));
        first.join().unwrap();
        second.join().unwrap();
    }

    #[tokio::test]
    async fn oversized_content_fails_without_trying_another_source() {
        let (address, task) = server(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nZIP!");
        let sources = vec![
            DownloadSource {
                name: "first".into(),
                prefix: address,
                enabled: true,
            },
            DownloadSource {
                name: "must-not-run".into(),
                prefix: "http://127.0.0.1:1/".into(),
                enabled: true,
            },
        ];
        let result = fetch(
            &Url::parse("https://github.com/example/repo/package.zip").unwrap(),
            &sources,
            3,
            |name, _, _| assert_ne!(name, "must-not-run"),
        )
        .await;
        assert!(result.unwrap_err().contains("大小限制"));
        task.join().unwrap();
    }

    #[tokio::test]
    async fn all_failed_sources_preserve_http_status_without_request_secrets() {
        let (first_url, first) = server(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n");
        let (second_url, second) = server(b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n");
        let sources = vec![
            DownloadSource {
                name: "first".into(),
                prefix: first_url.replacen("http://", "http://user:private-credential@", 1),
                enabled: true,
            },
            DownloadSource {
                name: "second".into(),
                prefix: second_url,
                enabled: true,
            },
        ];
        let attempts = Mutex::new(Vec::new());
        let error = fetch(
            &Url::parse("https://github.com/example/repo/package.zip?signature=private-query")
                .unwrap(),
            &sources,
            10,
            |name, _, _| attempts.lock().unwrap().push(name.to_string()),
        )
        .await
        .unwrap_err();
        assert!(error.contains("first（HTTP status client error (403 Forbidden)"));
        assert!(error.contains("second（HTTP status server error (503 Unavailable)"));
        for secret in [
            "private-credential",
            "private-query",
            "package.zip",
            "http://",
        ] {
            assert!(!error.contains(secret), "request details leaked: {error}");
        }
        assert_eq!(*attempts.lock().unwrap(), vec!["first", "second"]);
        first.join().unwrap();
        second.join().unwrap();
    }

    #[tokio::test]
    async fn interrupted_response_preserves_the_transport_cause() {
        let (address, task) = server(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nZIP!");
        let error = fetch(
            &Url::parse(&format!("{address}package.zip?signature=private-query")).unwrap(),
            &[],
            10,
            |_, _, _| {},
        )
        .await
        .unwrap_err();
        assert!(
            error.contains("Caused by:"),
            "transport cause lost: {error}"
        );
        assert!(error.contains("end of file before message length reached"));
        assert!(!error.contains("private-query"));
        task.join().unwrap();
    }
    #[test]
    fn ordered_sources_apply_to_raw_releases_and_archives_only() {
        let mut sources = defaults();
        sources[0].enabled = false;
        sources.swap(1, 2);
        for address in [
            "https://raw.githubusercontent.com/a/b/main/catalog.json",
            "https://github.com/a/b/releases/download/v1/plugin.zip",
            "https://github.com/a/b/archive/abc.zip",
        ] {
            let urls = candidates(&https_url(address).unwrap(), &sources);
            assert_eq!(urls[0].1.as_str(), address);
            assert_eq!(urls[1].1.as_str(), format!("https://ghproxy.vip/{address}"));
        }
        assert_eq!(
            candidates(
                &https_url("https://downloads.example.org/plugin.zip").unwrap(),
                &sources
            )
            .len(),
            1
        );
    }
    #[test]
    fn settings_preserve_other_domains_and_custom_order() {
        let dir = std::env::temp_dir().join(format!("sakura-sources-{}", uuid::Uuid::new_v4()));
        let repo = UiConfigRepository::new(dir.join("ui.json"));
        repo.update("TEST", |v| {
            v["settings"]["theme"] = json!("dark");
            v["settings"]["download_sources"] = json!([
            {"name":"Custom", "prefix":"https://mirror.example/", "enabled":true}]);
            Ok(())
        })
        .unwrap();
        assert_eq!(
            DownloadSources(repo.clone()).load().unwrap()[0].name,
            "Custom"
        );
        assert_eq!(repo.load("TEST").unwrap()["settings"]["theme"], "dark");
        std::fs::remove_dir_all(dir).unwrap();
    }
}
