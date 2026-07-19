//! Local worker supervisor.
//!
//! Responsibilities:
//! * Pick a free loopback port.
//! * Spawn the downloaded worker executable hidden (no console window).
//! * Inject provider configuration as environment — provider mode, the secrets
//!   pulled from the encrypted vault, the Beta storage namespace, and (managed
//!   mode) the gateway URL + installation token. Secrets go in the child env
//!   only: never to disk in plaintext, never on the command line.
//! * Wait for the worker's health endpoint before reporting ready.
//! * Attach the worker to a Windows Job Object with kill-on-close so it can
//!   never outlive the desktop app.
//! * Restart on demand, refused while a job is active.

use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::{provider, runtime, secrets};

pub struct WorkerHandle {
    child: Child,
    pub port: u16,
    #[cfg(windows)]
    _job: JobObject,
}

#[derive(Default)]
pub struct WorkerSupervisor {
    inner: Mutex<Option<WorkerHandle>>,
}

impl WorkerSupervisor {
    pub fn new() -> Self {
        Self { inner: Mutex::new(None) }
    }

    /// Base worker URL (e.g. `http://127.0.0.1:PORT/api`) when running.
    pub fn base_url(&self) -> Option<String> {
        let guard = self.inner.lock().ok()?;
        guard.as_ref().map(|h| format!("http://127.0.0.1:{}/api", h.port))
    }

    pub fn is_running(&self) -> bool {
        let mut guard = match self.inner.lock() {
            Ok(guard) => guard,
            Err(_) => return false,
        };
        if let Some(handle) = guard.as_mut() {
            // try_wait returns Ok(Some) once the child has exited.
            match handle.child.try_wait() {
                Ok(Some(_)) => {
                    *guard = None;
                    false
                }
                Ok(None) => true,
                Err(_) => true,
            }
        } else {
            false
        }
    }

    /// Start the worker for the given runtime version. No-op if already running.
    pub fn start(&self, base: &Path, version: &str) -> Result<u16, String> {
        let mut guard = self.inner.lock().map_err(|_| "supervisor lock poisoned".to_string())?;
        if let Some(handle) = guard.as_ref() {
            return Ok(handle.port);
        }
        let handle = spawn_worker(base, version)?;
        let port = handle.port;
        *guard = Some(handle);
        drop(guard);
        wait_for_health(port)?;
        Ok(port)
    }

    pub fn stop(&self) {
        if let Ok(mut guard) = self.inner.lock() {
            if let Some(mut handle) = guard.take() {
                let _ = handle.child.kill();
                let _ = handle.child.wait();
            }
        }
    }

    /// Restart with fresh provider env. Refused while `job_active` is true so a
    /// provider switch never interrupts an in-flight render/analysis.
    pub fn restart(&self, base: &Path, version: &str, job_active: bool) -> Result<u16, String> {
        if job_active {
            return Err("Cannot restart the worker while a job is running.".into());
        }
        self.stop();
        self.start(base, version)
    }
}

/// Build-time embedded default keys (comma-separated), baked in via env at
/// compile time. Empty when the build did not set them. These are a fallback;
/// a user-supplied key from the vault always comes first.
fn embedded_groq_keys() -> Vec<String> {
    split_keys(option_env!("CUTTOCLIP_EMBEDDED_GROQ_KEYS").unwrap_or(""))
}

fn embedded_gemini_keys() -> Vec<String> {
    split_keys(option_env!("CUTTOCLIP_EMBEDDED_GEMINI_KEYS").unwrap_or(""))
}

fn split_keys(raw: &str) -> Vec<String> {
    raw.split(',').map(|s| s.trim()).filter(|s| !s.is_empty()).map(|s| s.to_string()).collect()
}

/// User keys (from the vault, stored comma-joined) first, then embedded
/// defaults; blanks and duplicates removed while preserving order.
fn merge_keys(user_keys: Option<String>, embedded: Vec<String>) -> Vec<String> {
    let mut keys: Vec<String> = Vec::new();
    if let Some(stored) = user_keys {
        for key in split_keys(&stored) {
            if !keys.contains(&key) {
                keys.push(key);
            }
        }
    }
    for key in embedded {
        if !keys.contains(&key) {
            keys.push(key);
        }
    }
    keys
}

fn free_loopback_port() -> Result<u16, String> {
    // Bind to port 0 so the OS assigns a free port, read it, then drop the
    // listener. There is a small race before the worker binds, but the window is
    // tiny and the worker binds immediately on start.
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| format!("could not find a free port: {e}"))?;
    let port = listener.local_addr().map_err(|e| format!("could not read local port: {e}"))?.port();
    Ok(port)
}

fn worker_executable(base: &Path, version: &str) -> PathBuf {
    // PyInstaller onedir layout: <runtime>/<version>/worker/local-worker.exe
    runtime::runtime_root(base)
        .join(version)
        .join("worker")
        .join(if cfg!(windows) { "local-worker.exe" } else { "local-worker" })
}

fn spawn_worker(base: &Path, version: &str) -> Result<WorkerHandle, String> {
    let exe = worker_executable(base, version);
    if !exe.is_file() {
        return Err("The worker runtime is not installed.".into());
    }
    let port = free_loopback_port()?;
    let version_dir = runtime::runtime_root(base).join(version);

    let mut command = Command::new(&exe);
    command.env("CUTTOCLIP_PORT", port.to_string());
    command.env("CUTTOCLIP_APP_NAME", "CutToClip Beta");
    // Point the worker at the bundled binaries so it never depends on PATH.
    command.env("CUTTOCLIP_FFMPEG", version_dir.join("ffmpeg").join("ffmpeg.exe"));
    command.env("CUTTOCLIP_FFPROBE", version_dir.join("ffmpeg").join("ffprobe.exe"));
    command.env("CUTTOCLIP_DENO", version_dir.join("deno").join("deno.exe"));

    let embedded_groq = embedded_groq_keys();
    let embedded_gemini = embedded_gemini_keys();
    let has_embedded = !embedded_groq.is_empty() && !embedded_gemini.is_empty();
    // Fresh install + embedded keys => turnkey BYOK (no gateway). Otherwise the
    // user's stored choice wins.
    let mode = provider::effective_mode(base, has_embedded);
    command.env("CUTTOCLIP_PROVIDER_MODE", &mode);

    if mode == "byok" {
        // Secrets are injected as child env only — never to disk or argv. The
        // user's stored key (if any) is tried first, then build-time embedded
        // default keys, so testers get baked-in keys but can override them.
        let groq = merge_keys(secrets::get_secret(base, provider::KEY_GROQ)?, embedded_groq);
        if !groq.is_empty() {
            command.env("CUTTOCLIP_GROQ_API_KEYS", groq.join(","));
        }
        let gemini = merge_keys(secrets::get_secret(base, provider::KEY_GEMINI)?, embedded_gemini);
        if !gemini.is_empty() {
            command.env("CUTTOCLIP_GEMINI_API_KEYS", gemini.join(","));
        }
    } else {
        let config = provider::read_config(base);
        if let Some(url) = config.gateway_url.as_ref() {
            command.env("CUTTOCLIP_GATEWAY_URL", url);
        }
        if let Some(token) = secrets::get_secret(base, provider::KEY_INSTALLATION_TOKEN)? {
            command.env("CUTTOCLIP_INSTALLATION_TOKEN", token);
        }
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NO_WINDOW: run the worker hidden with no console popup.
        command.creation_flags(0x0800_0000);
    }

    let child = command.spawn().map_err(|e| format!("could not start the worker: {e}"))?;

    #[cfg(windows)]
    {
        let job = JobObject::new().map_err(|e| format!("could not create job object: {e}"))?;
        job.assign(&child).map_err(|e| format!("could not attach worker to job object: {e}"))?;
        Ok(WorkerHandle { child, port, _job: job })
    }
    #[cfg(not(windows))]
    {
        Ok(WorkerHandle { child, port })
    }
}

fn wait_for_health(port: u16) -> Result<(), String> {
    let url = format!("http://127.0.0.1:{port}/api/health");
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        let ok = Command::new("curl")
            .args(["-sS", "--max-time", "2", "-o", "/dev/null", "-w", "%{http_code}", &url])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim() == "200")
            .unwrap_or(false);
        if ok {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(400));
    }
    Err("The worker did not become healthy in time.".into())
}

// --- Windows Job Object: kill the worker when the app (job) closes ---

#[cfg(windows)]
struct JobObject {
    handle: windows_sys::Win32::Foundation::HANDLE,
}

// SAFETY: the raw job-object HANDLE is only ever touched while the owning
// WorkerHandle is held behind the supervisor's Mutex, so it is never accessed
// concurrently. The handle is otherwise inert. This lets AppState satisfy the
// Send + Sync bound that tauri::State requires.
#[cfg(windows)]
unsafe impl Send for JobObject {}
#[cfg(windows)]
unsafe impl Sync for JobObject {}

#[cfg(windows)]
impl JobObject {
    fn new() -> Result<Self, String> {
        use std::ptr;
        use windows_sys::Win32::System::JobObjects::{
            CreateJobObjectW, SetInformationJobObject, JobObjectExtendedLimitInformation,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };
        unsafe {
            let handle = CreateJobObjectW(ptr::null(), ptr::null());
            if handle.is_null() {
                return Err("CreateJobObjectW failed".into());
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                windows_sys::Win32::Foundation::CloseHandle(handle);
                return Err("SetInformationJobObject failed".into());
            }
            Ok(Self { handle })
        }
    }

    fn assign(&self, child: &Child) -> Result<(), String> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;
        unsafe {
            let ok = AssignProcessToJobObject(self.handle, child.as_raw_handle() as _);
            if ok == 0 {
                return Err("AssignProcessToJobObject failed".into());
            }
        }
        Ok(())
    }
}

#[cfg(windows)]
impl Drop for JobObject {
    fn drop(&mut self) {
        // Closing the last job handle triggers KILL_ON_JOB_CLOSE for the worker.
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.handle);
        }
    }
}
