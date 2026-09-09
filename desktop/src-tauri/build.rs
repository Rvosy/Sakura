fn main() {
    println!("cargo:rerun-if-changed=icons/icon.ico");
    println!("cargo:rerun-if-changed=icons/icon.png");
    println!("cargo:rerun-if-env-changed=SAKURA_BUILD_ID");
    println!("cargo:rerun-if-changed=../../.git/HEAD");
    println!("cargo:rerun-if-changed=release-staging/diagnostic-build-id.txt");
    let release = std::env::var("PROFILE").unwrap_or_default() == "release";
    let mut environment = "development".to_string();
    let build_id = if release {
        let staged = std::fs::read_to_string("release-staging/diagnostic-build-id.txt")
            .expect("release requires stage_distribution.py diagnostic mapping")
            .trim()
            .to_string();
        if let Ok(explicit) = std::env::var("SAKURA_BUILD_ID") {
            assert_eq!(explicit, staged, "build id must match staged resources");
        }
        let mapping: serde_json::Value = serde_json::from_slice(
            &std::fs::read("release-staging/diagnostic-build.json")
                .expect("missing source mapping"),
        )
        .expect("invalid mapping");
        assert_eq!(
            mapping["schemaVersion"].as_u64(),
            Some(3),
            "unsupported mapping schema"
        );
        assert_eq!(
            mapping["buildId"].as_str(),
            Some(staged.as_str()),
            "mapping build id mismatch"
        );
        environment = mapping["environment"]
            .as_str()
            .filter(|s| matches!(*s, "production" | "development" | "acceptance"))
            .expect("missing build environment")
            .into();
        for (key, base, path_key) in [
            ("sources", "../..", "file"),
            ("resources", "release-staging", "path"),
        ] {
            for entry in mapping[key].as_array().expect("missing resource mapping") {
                let relative = entry[path_key].as_str().expect("missing resource path");
                assert!(
                    !relative.starts_with('/') && !relative.split('/').any(|p| p == ".."),
                    "unsafe source mapping"
                );
                let path = std::path::Path::new(base).join(relative);
                println!("cargo:rerun-if-changed={}", path.display());
                assert!(path.is_file(), "mapped resource missing: {}", relative);
            }
        }
        staged
    } else {
        let commit = std::process::Command::new("git")
            .args(["rev-parse", "HEAD"])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .unwrap_or_else(|| "unknown".into());
        format!("development-{}", commit.trim())
    };
    assert!(
        !build_id.is_empty()
            && build_id.len() <= 128
            && build_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b)),
        "invalid build id"
    );
    println!("cargo:rustc-env=SAKURA_BUILD_ID={build_id}");
    println!("cargo:rustc-env=SAKURA_TELEMETRY_ENV={environment}");
    tauri_build::build()
}
