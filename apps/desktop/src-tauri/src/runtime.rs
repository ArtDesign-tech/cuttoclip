//! Runtime downloader and installer.
//!
//! The installer ships small; on first launch it fetches a runtime ZIP (worker,
//! FFmpeg/FFprobe, yt-dlp, Deno, vision models, fonts) from a GitHub Release and
//! installs it under `<local_app_data>/runtime/<version>`.
//!
//! Safety properties required by the spec:
//! * SHA-256 of the download is verified before extraction.
//! * ZIP entries are rejected if they escape the target dir (path traversal /
//!   absolute paths / `..`).
//! * Extraction is atomic: unpack into a temp dir, then rename into place, so a
//!   crash never leaves a half-installed runtime.
//! * Downloads resume with HTTP Range and can be cancelled.
//!
//! HTTPS uses the Windows-bundled `curl.exe` (see [[decision-rust-http-curl]]).

use std::fs;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::proc::curl_command;

/// A locked runtime descriptor. In the shipped build this comes from a bundled
/// `runtime-lock.json`; the URL/SHA/version are pinned at release time.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeLock {
    pub version: String,
    pub url: String,
    pub sha256: String,
    #[serde(default)]
    pub size_bytes: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapStatus {
    /// One of: "not-installed", "installed", "outdated".
    pub state: String,
    pub installed_version: Option<String>,
    pub required_version: Option<String>,
    pub runtime_dir: Option<String>,
    pub worker_running: bool,
    pub worker_base_url: Option<String>,
}

pub fn runtime_root(base: &Path) -> PathBuf {
    base.join("runtime")
}

fn version_dir(base: &Path, version: &str) -> PathBuf {
    runtime_root(base).join(version)
}

/// A runtime is considered installed when its version dir has a marker written
/// only after a fully verified, atomic install.
fn install_marker(base: &Path, version: &str) -> PathBuf {
    version_dir(base, version).join(".installed")
}

pub fn is_installed(base: &Path, version: &str) -> bool {
    install_marker(base, version).is_file()
}

pub fn bootstrap_status(base: &Path, lock: Option<&RuntimeLock>) -> BootstrapStatus {
    let required = lock.map(|l| l.version.clone());
    let installed = required.as_ref().filter(|v| is_installed(base, v)).cloned();
    let state = match (&required, &installed) {
        (Some(_), Some(_)) => "installed",
        (Some(_), None) => "not-installed",
        (None, _) => "not-installed",
    };
    BootstrapStatus {
        state: state.to_string(),
        installed_version: installed.clone(),
        required_version: required,
        runtime_dir: installed.map(|v| version_dir(base, &v).to_string_lossy().to_string()),
        // Worker liveness is owned by the supervisor; the caller fills these in.
        worker_running: false,
        worker_base_url: None,
    }
}

/// Download (with resume), verify SHA-256, and atomically extract the runtime.
/// `cancel` is polled between chunks and extraction entries.
pub fn install_runtime(
    base: &Path,
    lock: &RuntimeLock,
    cancel: &Arc<AtomicBool>,
) -> Result<(), String> {
    if is_installed(base, &lock.version) {
        return Ok(());
    }
    let root = runtime_root(base);
    fs::create_dir_all(&root).map_err(|e| format!("could not create runtime directory: {e}"))?;

    let archive = root.join(format!("{}.zip.part", lock.version));
    download_with_resume(&lock.url, &archive, cancel)?;
    if cancel.load(Ordering::Relaxed) {
        let _ = fs::remove_file(&archive);
        return Err("Runtime download was cancelled.".into());
    }

    verify_sha256(&archive, &lock.sha256).inspect_err(|_| {
        // A checksum mismatch means a corrupt/partial file; remove it so the next
        // attempt starts clean rather than resuming onto bad bytes.
        let _ = fs::remove_file(&archive);
    })?;

    let staging = root.join(format!(".staging-{}", lock.version));
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir_all(&staging).map_err(|e| format!("could not create staging directory: {e}"))?;

    let extract_result = extract_zip(&archive, &staging, cancel);
    if let Err(error) = extract_result {
        let _ = fs::remove_dir_all(&staging);
        let _ = fs::remove_file(&archive);
        return Err(error);
    }

    // Atomic install: move staging into the final version dir, then drop the marker.
    let final_dir = version_dir(base, &lock.version);
    let _ = fs::remove_dir_all(&final_dir);
    fs::rename(&staging, &final_dir)
        .map_err(|e| format!("could not finalize runtime install: {e}"))?;
    fs::write(install_marker(base, &lock.version), lock.version.as_bytes())
        .map_err(|e| format!("could not write install marker: {e}"))?;
    let _ = fs::remove_file(&archive);
    Ok(())
}

fn download_with_resume(url: &str, dest: &Path, cancel: &Arc<AtomicBool>) -> Result<(), String> {
    if cancel.load(Ordering::Relaxed) {
        return Err("Runtime download was cancelled.".into());
    }
    // curl -C - resumes from the current size of the partial file via HTTP Range;
    // --fail turns an HTTP error status into a non-zero exit; --retry adds
    // transient-failure retries.
    let status = curl_command()
        .args([
            "-fSL",
            "-C",
            "-",
            "--retry",
            "3",
            "--retry-delay",
            "2",
            "--max-time",
            "3600",
            "-o",
        ])
        .arg(dest)
        .arg(url)
        .status()
        .map_err(|e| format!("could not run curl: {e}"))?;
    if !status.success() {
        return Err("The runtime download failed.".into());
    }
    Ok(())
}

fn verify_sha256(path: &Path, expected_hex: &str) -> Result<(), String> {
    let mut file = fs::File::open(path).map_err(|e| format!("could not open download: {e}"))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer).map_err(|e| format!("could not read download: {e}"))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    let actual = hex::encode(hasher.finalize());
    if !actual.eq_ignore_ascii_case(expected_hex.trim()) {
        return Err("The runtime download failed its integrity check.".into());
    }
    Ok(())
}

fn extract_zip(archive: &Path, dest: &Path, cancel: &Arc<AtomicBool>) -> Result<(), String> {
    let file = fs::File::open(archive).map_err(|e| format!("could not open archive: {e}"))?;
    let mut zip = zip::ZipArchive::new(file).map_err(|e| format!("could not read archive: {e}"))?;
    for index in 0..zip.len() {
        if cancel.load(Ordering::Relaxed) {
            return Err("Runtime install was cancelled.".into());
        }
        let mut entry = zip.by_index(index).map_err(|e| format!("could not read archive entry: {e}"))?;
        let raw = entry
            .enclosed_name()
            .ok_or_else(|| "The archive contains an unsafe path.".to_string())?;
        let safe = sanitize_entry_path(dest, &raw)?;
        if entry.is_dir() {
            fs::create_dir_all(&safe).map_err(|e| format!("could not create directory: {e}"))?;
            continue;
        }
        if let Some(parent) = safe.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("could not create directory: {e}"))?;
        }
        let mut out = fs::File::create(&safe).map_err(|e| format!("could not write file: {e}"))?;
        std::io::copy(&mut entry, &mut out).map_err(|e| format!("could not extract file: {e}"))?;
    }
    Ok(())
}

/// Reject absolute paths, `..` components, and anything that would escape `dest`.
/// Returns the safe joined path inside `dest`.
fn sanitize_entry_path(dest: &Path, entry: &Path) -> Result<PathBuf, String> {
    let mut safe = dest.to_path_buf();
    for component in entry.components() {
        match component {
            Component::Normal(part) => safe.push(part),
            Component::CurDir => {}
            // Prefix (C:), RootDir (/), and ParentDir (..) are all rejected: none
            // may appear in a trusted, relative archive entry.
            Component::Prefix(_) | Component::RootDir | Component::ParentDir => {
                return Err("The archive contains an unsafe path.".into());
            }
        }
    }
    // Defense in depth: the joined path must still be within dest.
    if !safe.starts_with(dest) {
        return Err("The archive contains an unsafe path.".into());
    }
    Ok(safe)
}
