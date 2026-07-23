mod proc;
mod provider;
mod runtime;
mod secrets;
mod supervisor;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use provider::InstallationIdentity;
use runtime::{BootstrapStatus, RuntimeLock};
use supervisor::WorkerSupervisor;

/// The runtime descriptor pinned at release time. `url`/`sha256` are filled in by
/// track F before building the installer; an empty `url` means "no runtime is
/// published for this build yet" and install is refused with a clear message.
const RUNTIME_LOCK_JSON: &str = include_str!("../runtime-lock.json");

fn runtime_lock() -> Option<RuntimeLock> {
    let lock: RuntimeLock = serde_json::from_str(RUNTIME_LOCK_JSON).ok()?;
    Some(lock)
}

struct AppState {
    supervisor: WorkerSupervisor,
    install_cancel: Arc<AtomicBool>,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    use tauri::Manager;

    fn local_data_dir(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
        app.path()
            .app_local_data_dir()
            .map_err(|e| format!("could not resolve local app data directory: {e}"))
    }

    #[tauri::command]
    async fn start_worker(app: tauri::AppHandle, state: tauri::State<'_, AppState>) -> Result<(), String> {
        let base = local_data_dir(&app)?;
        let lock = runtime_lock().ok_or_else(|| "The runtime descriptor is invalid.".to_string())?;
        state.supervisor.start(&base, &lock.version)?;
        Ok(())
    }

    #[tauri::command]
    fn reveal_output(path: String) -> Result<(), String> {
        let output = std::path::PathBuf::from(path)
            .canonicalize()
            .map_err(|error| error.to_string())?;
        if output.extension().and_then(|value| value.to_str()) != Some("mp4") || !output.is_file() {
            return Err("Only an existing MP4 output can be revealed.".into());
        }
        #[cfg(target_os = "windows")]
        {
            std::process::Command::new("explorer.exe")
                .arg(format!("/select,{}", output.display()))
                .spawn()
                .map_err(|error| error.to_string())?;
            Ok(())
        }
        #[cfg(not(target_os = "windows"))]
        {
            let _ = output;
            Err("Reveal in folder is currently supported on Windows.".into())
        }
    }

    // --- Provider commands (C1) ---

    #[tauri::command]
    fn activate_managed(app: tauri::AppHandle, invite_code: String) -> Result<(), String> {
        let base = local_data_dir(&app)?;
        provider::activate_managed(&base, &invite_code)
    }

    #[tauri::command]
    fn save_byok_credentials(
        app: tauri::AppHandle,
        groq_key: String,
        gemini_key: String,
    ) -> Result<(), String> {
        let base = local_data_dir(&app)?;
        provider::save_byok_credentials(&base, &groq_key, &gemini_key)
    }

    #[tauri::command]
    fn clear_byok_credentials(app: tauri::AppHandle) -> Result<(), String> {
        let base = local_data_dir(&app)?;
        provider::clear_byok_credentials(&base)
    }

    #[tauri::command]
    fn set_provider_mode(app: tauri::AppHandle, mode: String) -> Result<(), String> {
        let base = local_data_dir(&app)?;
        provider::set_provider_mode(&base, &mode)
    }

    #[tauri::command]
    fn installation_identity(app: tauri::AppHandle) -> Result<Option<InstallationIdentity>, String> {
        let base = local_data_dir(&app)?;
        provider::installation_identity(&base)
    }

    #[tauri::command]
    async fn restart_worker(
        app: tauri::AppHandle,
        state: tauri::State<'_, AppState>,
        job_active: Option<bool>,
    ) -> Result<(), String> {
        let base = local_data_dir(&app)?;
        let lock = runtime_lock().ok_or_else(|| "The runtime descriptor is invalid.".to_string())?;
        state.supervisor.restart(&base, &lock.version, job_active.unwrap_or(false))?;
        Ok(())
    }

    // --- Runtime bootstrap commands (C2) ---

    #[tauri::command]
    fn bootstrap_status(app: tauri::AppHandle, state: tauri::State<'_, AppState>) -> Result<BootstrapStatus, String> {
        let base = local_data_dir(&app)?;
        let mut status = runtime::bootstrap_status(&base, runtime_lock().as_ref());
        status.worker_running = state.supervisor.is_running();
        status.worker_base_url = state.supervisor.base_url();
        Ok(status)
    }

    #[tauri::command]
    async fn install_runtime(app: tauri::AppHandle, state: tauri::State<'_, AppState>) -> Result<(), String> {
        let base = local_data_dir(&app)?;
        let lock = runtime_lock().ok_or_else(|| "The runtime descriptor is invalid.".to_string())?;
        if lock.url.trim().is_empty() || lock.sha256.trim().is_empty() {
            return Err("No runtime has been published for this build yet.".into());
        }
        state.install_cancel.store(false, Ordering::Relaxed);
        let cancel = Arc::clone(&state.install_cancel);
        // Download + extraction is blocking; run it off the async runtime so the
        // command future doesn't peg an executor thread.
        let result = tauri::async_runtime::spawn_blocking(move || {
            runtime::install_runtime(&base, &lock, &cancel)
        })
        .await
        .map_err(|e| format!("runtime install task failed: {e}"))?;
        result
    }

    #[tauri::command]
    fn cancel_runtime_install(state: tauri::State<'_, AppState>) -> Result<(), String> {
        state.install_cancel.store(true, Ordering::Relaxed);
        Ok(())
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(AppState {
            supervisor: WorkerSupervisor::new(),
            install_cancel: Arc::new(AtomicBool::new(false)),
        })
        .setup(|app| {
            let salt_path = app
                .path()
                .app_local_data_dir()
                .expect("could not resolve local app data directory")
                .join("salt.txt");
            app.handle()
                .plugin(tauri_plugin_stronghold::Builder::with_argon2(&salt_path).build())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Stop the worker when the main window closes so it can never be orphaned
            // (the Job Object is the hard backstop; this is the graceful path).
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<AppState>() {
                    state.supervisor.stop();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            start_worker,
            reveal_output,
            activate_managed,
            save_byok_credentials,
            clear_byok_credentials,
            set_provider_mode,
            installation_identity,
            restart_worker,
            bootstrap_status,
            install_runtime,
            cancel_runtime_install
        ])
        .run(tauri::generate_context!())
        .expect("error while running CutToClip");
}
