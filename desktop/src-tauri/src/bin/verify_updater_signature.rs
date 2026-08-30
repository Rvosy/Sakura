use std::{env, ffi::OsString, fs, path::PathBuf, process::ExitCode};

use base64::{engine::general_purpose::STANDARD, Engine as _};
use minisign_verify::{PublicKey, Signature};

fn decode_public_key(encoded: &str) -> Result<PublicKey, String> {
    let decoded = STANDARD
        .decode(encoded.trim())
        .map_err(|_| "UPDATER_PUBLIC_KEY_BASE64_INVALID".to_string())?;
    let document =
        String::from_utf8(decoded).map_err(|_| "UPDATER_PUBLIC_KEY_UTF8_INVALID".to_string())?;
    PublicKey::decode(&document).map_err(|_| "UPDATER_PUBLIC_KEY_INVALID".to_string())
}

fn decode_signature(encoded: &str) -> Result<Signature, String> {
    let decoded = STANDARD
        .decode(encoded.trim())
        .map_err(|_| "UPDATER_SIGNATURE_BASE64_INVALID".to_string())?;
    let document =
        String::from_utf8(decoded).map_err(|_| "UPDATER_SIGNATURE_UTF8_INVALID".to_string())?;
    Signature::decode(&document).map_err(|_| "UPDATER_SIGNATURE_INVALID".to_string())
}

fn verify(public_key: &str, artifact: &[u8], signature: &str) -> Result<(), String> {
    let public_key = decode_public_key(public_key)?;
    let signature = decode_signature(signature)?;
    public_key
        .verify(artifact, &signature, true)
        .map_err(|_| "UPDATER_SIGNATURE_VERIFICATION_FAILED".to_string())
}

fn required_path(value: Option<OsString>, code: &str) -> Result<PathBuf, String> {
    value.map(PathBuf::from).ok_or_else(|| code.to_string())
}

fn run() -> Result<PathBuf, String> {
    let mut arguments = env::args_os().skip(1);
    let artifact_path = required_path(arguments.next(), "UPDATER_ARTIFACT_PATH_REQUIRED")?;
    let signature_path = required_path(arguments.next(), "UPDATER_SIGNATURE_PATH_REQUIRED")?;
    if arguments.next().is_some() {
        return Err("UPDATER_SIGNATURE_ARGUMENTS_INVALID".to_string());
    }
    let public_key = env::var("SAKURA_UPDATER_PUBLIC_KEY")
        .map_err(|_| "UPDATER_PUBLIC_KEY_REQUIRED".to_string())?;
    let artifact =
        fs::read(&artifact_path).map_err(|_| "UPDATER_ARTIFACT_READ_FAILED".to_string())?;
    let signature = fs::read_to_string(&signature_path)
        .map_err(|_| "UPDATER_SIGNATURE_READ_FAILED".to_string())?;
    verify(&public_key, &artifact, &signature)?;
    Ok(artifact_path)
}

fn main() -> ExitCode {
    match run() {
        Ok(path) => {
            println!("Verified updater signature: {}", path.display());
            ExitCode::SUCCESS
        }
        Err(code) => {
            eprintln!("{code}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PUBLIC_KEY: &str = "untrusted comment: minisign public key\nRWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3\n";
    const SIGNATURE: &str = "untrusted comment: signature from minisign secret key\nRWQf6LRCGA9i59SLOFxz6NxvASXDJeRtuZykwQepbDEGt87ig1BNpWaVWuNrm73YiIiJbq71Wi+dP9eKL8OC351vwIasSSbXxwA=\ntrusted comment: timestamp:1555779966\tfile:test\nQtKMXWyYcwdpZAlPF7tE2ENJkRd1ujvKjlj1m9RtHTBnZPa5WKU5uWRs5GoP5M/VqE81QFuMKI5k/SfNQUaOAA==\n";

    #[test]
    fn verifies_the_same_outer_base64_contract_used_by_tauri() {
        let public_key = STANDARD.encode(PUBLIC_KEY);
        let signature = STANDARD.encode(SIGNATURE);
        verify(&public_key, b"test", &signature).expect("known minisign fixture");
        assert_eq!(
            verify(&public_key, b"changed", &signature),
            Err("UPDATER_SIGNATURE_VERIFICATION_FAILED".to_string())
        );
    }
}
