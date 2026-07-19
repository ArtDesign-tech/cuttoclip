//! Provider configuration and Managed-Beta activation.
//!
//! The provider MODE (`managed` | `byok`) is non-secret and lives in
//! `<app_local_data>/config.json`; the worker reads it (via injected env) when it
//! starts. SECRETS — the installation token and BYOK keys — go only through
//! [`crate::secrets`] (DPAPI + AES-GCM), never into that JSON.
//!
//! HTTPS to the gateway (invite activation + `/v1/me`) uses the Windows-bundled
//! `curl.exe` (SChannel), so no Rust TLS stack is required. Secrets are passed to
//! curl through stdin — the invite as a request body, the bearer token as a
//! `--config -` header block — so they never appear on the command line.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::{Deserialize, Serialize};

use crate::secrets;

pub const KEY_INSTALLATION_TOKEN: &str = "installation_token";
pub const KEY_GROQ: &str = "groq_api_key";
pub const KEY_GEMINI: &str = "gemini_api_key";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderConfig {
    #[serde(default = "default_mode")]
    pub provider_mode: String,
    #[serde(default)]
    pub gateway_url: Option<String>,
}

fn default_mode() -> String {
    "managed".to_string()
}

impl Default for ProviderConfig {
    fn default() -> Self {
        Self { provider_mode: default_mode(), gateway_url: None }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallationIdentity {
    pub installation_id: String,
    pub label: String,
    pub created_at: Option<String>,
    pub last_used_at: Option<String>,
}

fn config_path(base: &Path) -> PathBuf {
    base.join("config.json")
}

pub fn read_config(base: &Path) -> ProviderConfig {
    let path = config_path(base);
    match std::fs::read(&path) {
        Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_default(),
        Err(_) => ProviderConfig::default(),
    }
}

/// Whether the user has ever made a provider choice (config file written by
/// onboarding / set_provider_mode). Distinguishes a fresh install from a
/// deliberate "managed" choice, since both read back as mode "managed".
pub fn config_exists(base: &Path) -> bool {
    config_path(base).is_file()
}

/// The mode to actually run in. On a fresh install (no config yet) with keys
/// embedded at build time, default to BYOK so the app is turnkey and never
/// touches the operator gateway. Once the user has chosen, honor the config.
pub fn effective_mode(base: &Path, has_embedded_keys: bool) -> String {
    if !config_exists(base) && has_embedded_keys {
        return "byok".to_string();
    }
    read_config(base).provider_mode
}

fn write_config(base: &Path, config: &ProviderConfig) -> Result<(), String> {
    std::fs::create_dir_all(base).map_err(|e| format!("could not create config directory: {e}"))?;
    let bytes = serde_json::to_vec_pretty(config).map_err(|e| format!("could not serialize config: {e}"))?;
    let path = config_path(base);
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, &bytes).map_err(|e| format!("could not write config: {e}"))?;
    std::fs::rename(&tmp, &path).map_err(|e| format!("could not commit config: {e}"))
}

pub fn set_provider_mode(base: &Path, mode: &str) -> Result<(), String> {
    if mode != "managed" && mode != "byok" {
        return Err(format!("unknown provider mode: {mode}"));
    }
    let mut config = read_config(base);
    config.provider_mode = mode.to_string();
    write_config(base, &config)
}

pub fn save_byok_credentials(base: &Path, groq_key: &str, gemini_key: &str) -> Result<(), String> {
    if groq_key.trim().is_empty() {
        return Err("A Groq API key is required.".into());
    }
    if gemini_key.trim().is_empty() {
        return Err("A Gemini API key is required.".into());
    }
    secrets::set_secret(base, KEY_GROQ, groq_key.trim())?;
    secrets::set_secret(base, KEY_GEMINI, gemini_key.trim())?;
    Ok(())
}

pub fn clear_byok_credentials(base: &Path) -> Result<(), String> {
    secrets::remove_secrets(base, &[KEY_GROQ, KEY_GEMINI])
}

/// Gateway base URL. Baked in at build time via `CUTTOCLIP_GATEWAY_URL`, and
/// overridable at runtime by the same env var for local testing. The stored
/// config value (set on a successful activation) is preferred over both.
fn gateway_base_url(base: &Path) -> Option<String> {
    if let Some(url) = read_config(base).gateway_url {
        if !url.trim().is_empty() {
            return Some(url);
        }
    }
    if let Ok(url) = std::env::var("CUTTOCLIP_GATEWAY_URL") {
        if !url.trim().is_empty() {
            return Some(url);
        }
    }
    option_env!("CUTTOCLIP_GATEWAY_URL").map(|s| s.to_string()).filter(|s| !s.trim().is_empty())
}

/// Exchange a one-time invite for an installation token, then store the token in
/// the encrypted vault and remember the gateway URL in config.
pub fn activate_managed(base: &Path, invite_code: &str) -> Result<(), String> {
    let invite = invite_code.trim();
    if invite.is_empty() {
        return Err("An invite code is required.".into());
    }
    let gateway = gateway_base_url(base)
        .ok_or_else(|| "The gateway URL is not configured for this build.".to_string())?;
    let url = format!("{}/v1/activate", gateway.trim_end_matches('/'));

    // Invite travels only in the request body over stdin — never on the command line.
    let body = serde_json::json!({ "inviteCode": invite });
    let stdout = curl_post_json(&url, &body.to_string())?;

    let parsed: serde_json::Value =
        serde_json::from_str(&stdout).map_err(|_| "The gateway returned an unexpected response.".to_string())?;
    if let Some(token) = parsed.get("token").and_then(|v| v.as_str()) {
        secrets::set_secret(base, KEY_INSTALLATION_TOKEN, token)?;
        let mut config = read_config(base);
        config.gateway_url = Some(gateway);
        write_config(base, &config)?;
        return Ok(());
    }
    Err(gateway_error_message(&parsed).unwrap_or_else(|| "Invite code is invalid or already used.".to_string()))
}

/// Read the Managed installation identity from `GET /v1/me`. Returns Ok(None)
/// when there is no stored token (i.e. not yet activated).
pub fn installation_identity(base: &Path) -> Result<Option<InstallationIdentity>, String> {
    let token = match secrets::get_secret(base, KEY_INSTALLATION_TOKEN)? {
        Some(token) => token,
        None => return Ok(None),
    };
    let gateway = gateway_base_url(base)
        .ok_or_else(|| "The gateway URL is not configured for this build.".to_string())?;
    let url = format!("{}/v1/me", gateway.trim_end_matches('/'));

    // Bearer token is passed via a curl config on stdin, not argv.
    let stdout = curl_get_authed(&url, &token)?;
    let identity: InstallationIdentity =
        serde_json::from_str(&stdout).map_err(|_| "The gateway returned an unexpected identity response.".to_string())?;
    Ok(Some(identity))
}

fn gateway_error_message(parsed: &serde_json::Value) -> Option<String> {
    parsed
        .get("error")
        .and_then(|e| e.get("message"))
        .and_then(|m| m.as_str())
        .map(|s| s.to_string())
}

fn curl_post_json(url: &str, body: &str) -> Result<String, String> {
    let mut child = Command::new("curl")
        .args([
            "-sS",
            "--max-time",
            "30",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "-H",
            "Accept: application/json",
            "--data-binary",
            "@-",
            url,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("could not run curl: {e}"))?;
    child
        .stdin
        .take()
        .ok_or_else(|| "could not open curl stdin".to_string())?
        .write_all(body.as_bytes())
        .map_err(|e| format!("could not send request body: {e}"))?;
    let output = child.wait_with_output().map_err(|e| format!("curl failed: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "The gateway could not be reached: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn curl_get_authed(url: &str, token: &str) -> Result<String, String> {
    // Installation tokens are base64url; reject anything else so it can't break
    // out of the curl config quoting or inject extra options.
    if !token.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') {
        return Err("The stored installation token is malformed.".into());
    }
    let config = format!("url = \"{url}\"\nrequest = \"GET\"\nheader = \"Authorization: Bearer {token}\"\nheader = \"Accept: application/json\"\n");
    let mut child = Command::new("curl")
        .args(["-sS", "--max-time", "30", "--config", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("could not run curl: {e}"))?;
    child
        .stdin
        .take()
        .ok_or_else(|| "could not open curl stdin".to_string())?
        .write_all(config.as_bytes())
        .map_err(|e| format!("could not send request config: {e}"))?;
    let output = child.wait_with_output().map_err(|e| format!("curl failed: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "The gateway could not be reached: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}
