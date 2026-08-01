use crate::error::{BridgeError, BridgeResult};
use crate::protocol::{
    parse_worker_event, validate_app_generated_identifier, LogLevel, WorkerAck, WorkerEvent,
    WorkerRequest, MAX_EVENT_BYTES,
};
use crate::secrets::{SecretStore, WORKER_KEY_ENV};
use serde::Serialize;
use std::env;
use std::ffi::OsString;
#[cfg(not(debug_assertions))]
use std::fs::File;
#[cfg(not(debug_assertions))]
use std::io::Read;
use std::path::{Path, PathBuf};
#[cfg(not(debug_assertions))]
use std::sync::OnceLock;
use std::sync::{Arc, Mutex, MutexGuard};
#[cfg(not(debug_assertions))]
use tauri::path::BaseDirectory;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use zeroize::Zeroizing;

#[cfg(not(debug_assertions))]
use sha2::{Digest, Sha256};

const EVENT_CHANNEL: &str = "worker-event";
#[cfg(not(debug_assertions))]
const RELEASE_SIDECAR_NAME: &str = "aistereo-worker";

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkerStatus {
    Stopped,
    Running,
    Faulted,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerSnapshot {
    pub status: WorkerStatus,
    pub pid: Option<u32>,
    pub launch_mode: &'static str,
}

struct WorkerSlot {
    child: Option<CommandChild>,
    generation: u64,
    status: WorkerStatus,
    pid: Option<u32>,
}

impl Default for WorkerSlot {
    fn default() -> Self {
        Self {
            child: None,
            generation: 0,
            status: WorkerStatus::Stopped,
            pid: None,
        }
    }
}

#[derive(Clone, Default)]
pub struct WorkerManager {
    slot: Arc<Mutex<WorkerSlot>>,
    lifecycle: Arc<Mutex<()>>,
}

impl WorkerManager {
    pub fn snapshot(&self) -> BridgeResult<WorkerSnapshot> {
        let slot = self.lock_slot()?;
        Ok(WorkerSnapshot {
            status: slot.status,
            pid: slot.pid,
            launch_mode: launch_mode(),
        })
    }

    pub fn send(&self, app: &AppHandle, request: &WorkerRequest) -> BridgeResult<WorkerAck> {
        request.validate()?;
        let pid = self.ensure_started(app)?;
        let mut encoded = serde_json::to_vec(request)
            .map_err(|_| BridgeError::invalid("failed to encode worker request"))?;
        encoded.push(b'\n');

        let mut slot = self.lock_slot()?;
        let child = slot
            .child
            .as_mut()
            .ok_or_else(|| BridgeError::worker("worker stopped before request could be sent"))?;
        child
            .write(&encoded)
            .map_err(|_| BridgeError::worker("failed to write to the Python worker"))?;

        Ok(WorkerAck {
            accepted: true,
            id: request.id.clone(),
            worker_pid: pid,
        })
    }

    pub fn restart(&self, app: &AppHandle) -> BridgeResult<WorkerSnapshot> {
        let _lifecycle = self.lock_lifecycle()?;
        self.stop_locked()?;
        self.start_locked(app)?;
        self.snapshot()
    }

    pub fn stop(&self) -> BridgeResult<bool> {
        let _lifecycle = self.lock_lifecycle()?;
        self.stop_locked()
    }

    fn ensure_started(&self, app: &AppHandle) -> BridgeResult<u32> {
        let _lifecycle = self.lock_lifecycle()?;
        if let Some(pid) = self.lock_slot()?.pid {
            return Ok(pid);
        }
        self.start_locked(app)
    }

    fn start_locked(&self, app: &AppHandle) -> BridgeResult<u32> {
        if let Some(pid) = self.lock_slot()?.pid {
            return Ok(pid);
        }

        let (command, stderr_secret) = worker_command(app)?;
        let (mut receiver, child) = command
            .spawn()
            .map_err(|_| BridgeError::worker(worker_start_error_message()))?;
        let pid = child.pid();
        let generation = {
            let mut slot = self.lock_slot()?;
            slot.generation = slot.generation.wrapping_add(1);
            slot.child = Some(child);
            slot.status = WorkerStatus::Running;
            slot.pid = Some(pid);
            slot.generation
        };

        let manager = self.clone();
        let app_handle = app.clone();
        tauri::async_runtime::spawn(async move {
            let mut received_termination = false;
            while let Some(event) = receiver.recv().await {
                match event {
                    CommandEvent::Stdout(bytes) => relay_stdout(&app_handle, &bytes),
                    CommandEvent::Stderr(bytes) => {
                        let message = stderr_diagnostic(
                            &bytes,
                            stderr_secret.as_ref().map(|secret| secret.as_str()),
                        );
                        emit(
                            &app_handle,
                            &WorkerEvent::host_log(LogLevel::Error, message),
                        );
                    }
                    CommandEvent::Error(message) => {
                        emit(
                            &app_handle,
                            &WorkerEvent::lifecycle_error(
                                "worker_io_error",
                                redact_diagnostic(
                                    &bounded_text(&message),
                                    stderr_secret.as_ref().map(|secret| secret.as_str()),
                                ),
                            ),
                        );
                    }
                    CommandEvent::Terminated(payload) => {
                        received_termination = true;
                        let unexpected = manager.finish_generation(generation);
                        if unexpected {
                            let message = format!(
                                "Python worker exited unexpectedly (code: {:?}, signal: {:?})",
                                payload.code, payload.signal
                            );
                            emit(
                                &app_handle,
                                &WorkerEvent::lifecycle_error("worker_terminated", message),
                            );
                        }
                        break;
                    }
                    _ => {}
                }
            }

            if !received_termination && manager.finish_generation(generation) {
                emit(
                    &app_handle,
                    &WorkerEvent::lifecycle_error(
                        "worker_channel_closed",
                        "Python worker event channel closed unexpectedly",
                    ),
                );
            }
        });

        emit(
            app,
            &WorkerEvent::host_log(LogLevel::Info, format!("Python worker started (pid {pid})")),
        );
        Ok(pid)
    }

    fn stop_locked(&self) -> BridgeResult<bool> {
        let child = {
            let mut slot = self.lock_slot()?;
            let child = slot.child.take();
            slot.pid = None;
            slot.status = WorkerStatus::Stopped;
            child
        };

        let Some(child) = child else {
            return Ok(false);
        };
        child.kill().map_err(|_| {
            if let Ok(mut slot) = self.slot.lock() {
                slot.status = WorkerStatus::Faulted;
            }
            BridgeError::worker("failed to terminate the Python worker")
        })?;
        Ok(true)
    }

    /// Returns true only when the generation was active and ended without an
    /// explicit stop/restart request.
    fn finish_generation(&self, generation: u64) -> bool {
        let Ok(mut slot) = self.slot.lock() else {
            return false;
        };
        if slot.generation != generation || slot.child.is_none() {
            return false;
        }
        slot.child.take();
        slot.pid = None;
        slot.status = WorkerStatus::Faulted;
        true
    }

    fn lock_slot(&self) -> BridgeResult<MutexGuard<'_, WorkerSlot>> {
        self.slot
            .lock()
            .map_err(|_| BridgeError::internal("worker state lock was poisoned"))
    }

    fn lock_lifecycle(&self) -> BridgeResult<MutexGuard<'_, ()>> {
        self.lifecycle
            .lock()
            .map_err(|_| BridgeError::internal("worker lifecycle lock was poisoned"))
    }
}

fn worker_command(
    app: &AppHandle,
) -> BridgeResult<(
    tauri_plugin_shell::process::Command,
    Option<Zeroizing<String>>,
)> {
    let llm_key = app.state::<SecretStore>().load_for_worker()?;

    #[cfg(debug_assertions)]
    {
        let project_root = development_project_root()?;
        let python_root = project_root.join("python");
        let virtualenv_python = if cfg!(windows) {
            project_root
                .join(".venv")
                .join("Scripts")
                .join("python.exe")
        } else {
            project_root.join(".venv").join("bin").join("python")
        };
        let python_program = env::var_os("AISTEREO_PYTHON")
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| {
                if virtualenv_python.is_file() {
                    virtualenv_python.into_os_string()
                } else {
                    OsString::from("python")
                }
            });

        let mut python_paths = vec![python_root];
        if let Some(existing) = env::var_os("PYTHONPATH") {
            python_paths.extend(env::split_paths(&existing));
        }
        let python_path = env::join_paths(python_paths)
            .map_err(|_| BridgeError::worker("could not construct the development PYTHONPATH"))?;
        let media_tools = development_media_tools(&project_root)?;

        // Resolve before the builder takes ownership of the project root.
        let depth_model = development_depth_model(&project_root);
        let mut command = app
            .shell()
            .command(python_program)
            .args(["-u", "-m", "aistereo.worker"])
            .current_dir(project_root)
            .env("PYTHONPATH", python_path)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("AISTEREO_FFMPEG_PATH", media_tools.ffmpeg)
            .env("AISTEREO_FFPROBE_PATH", media_tools.ffprobe)
            .env("PATH", media_tools.path);
        // A developer-provisioned depth model is picked up the same way FFmpeg
        // is, so evaluating the neural backend needs no shell configuration.
        if let Some(model) = depth_model {
            command = command
                .env("AISTEREO_DEPTH_MODEL_PATH", model.checkpoint)
                .env("AISTEREO_DEPTH_MODEL_SOURCE", model.source);
        }
        if let Some(key) = llm_key.as_ref() {
            command = command.env(WORKER_KEY_ENV, key.as_str());
        }
        Ok((command, llm_key))
    }

    #[cfg(not(debug_assertions))]
    {
        let resources = release_resources(app)?;
        let mut command = app
            .shell()
            .sidecar(RELEASE_SIDECAR_NAME)
            .map_err(|_| BridgeError::worker(worker_start_error_message()))?
            .env("AISTEREO_FFMPEG_PATH", resources.ffmpeg)
            .env("AISTEREO_FFPROBE_PATH", resources.ffprobe)
            .env("PATH", resources.path)
            // Override, rather than inherit, any developer-machine model path.
            // An empty value keeps final export locked when no reviewed bundled
            // model was verified at build/runtime.
            .env(
                "AISTEREO_DEPTH_MODEL_PATH",
                resources.depth_model.unwrap_or_default(),
            );
        if let Some(key) = llm_key.as_ref() {
            command = command.env(WORKER_KEY_ENV, key.as_str());
        }
        Ok((command, llm_key))
    }
}

#[cfg(debug_assertions)]
struct DevelopmentMediaTools {
    ffmpeg: PathBuf,
    ffprobe: PathBuf,
    path: OsString,
}

#[cfg(debug_assertions)]
fn development_media_tools(project_root: &Path) -> BridgeResult<DevelopmentMediaTools> {
    let configured_ffmpeg = env::var_os("AISTEREO_FFMPEG_PATH").filter(|value| !value.is_empty());
    let configured_ffprobe = env::var_os("AISTEREO_FFPROBE_PATH").filter(|value| !value.is_empty());

    let pair = match (configured_ffmpeg, configured_ffprobe) {
        (Some(ffmpeg), Some(ffprobe)) => Some((PathBuf::from(ffmpeg), PathBuf::from(ffprobe))),
        (Some(_), None) | (None, Some(_)) => {
            return Err(BridgeError::worker(
                "AISTEREO_FFMPEG_PATH and AISTEREO_FFPROBE_PATH must be configured together",
            ));
        }
        (None, None) => {
            let local_bin = project_root
                .join(".dev-tools")
                .join("ffmpeg")
                .join("8.1.2")
                .join("bin");
            let local_pair = (local_bin.join("ffmpeg.exe"), local_bin.join("ffprobe.exe"));
            if local_pair.0.is_file() && local_pair.1.is_file() {
                Some(local_pair)
            } else {
                development_path_tool("ffmpeg").zip(development_path_tool("ffprobe"))
            }
        }
    }
    .ok_or_else(|| {
        BridgeError::worker(
            "FFmpeg and FFprobe are required for desktop development; run scripts/bootstrap.ps1 -ProvisionMediaTools",
        )
    })?;

    development_media_tool_pair(&pair.0, &pair.1)
}

#[cfg(debug_assertions)]
struct DevelopmentDepthModel {
    checkpoint: PathBuf,
    source: PathBuf,
}

/// Locate a project-local upstream checkpoint and its source tree.
///
/// An explicitly configured pair always wins. Otherwise the newest checkpoint in
/// `.dev-tools/depth` is used, so dropping a file there is all it takes.
#[cfg(debug_assertions)]
fn development_depth_model(project_root: &Path) -> Option<DevelopmentDepthModel> {
    let configured = env::var_os("AISTEREO_DEPTH_MODEL_PATH").filter(|value| !value.is_empty());
    let source_root = project_root.join(".dev-tools").join("depth");
    let source = env::var_os("AISTEREO_DEPTH_MODEL_SOURCE")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| source_root.join("Video-Depth-Anything"));
    if !source.join("video_depth_anything").is_dir() {
        return None;
    }
    let checkpoint = match configured {
        Some(path) => PathBuf::from(path),
        None => {
            let mut candidates: Vec<PathBuf> = std::fs::read_dir(&source_root)
                .ok()?
                .filter_map(|entry| entry.ok().map(|entry| entry.path()))
                .filter(|path| path.extension().is_some_and(|value| value == "pth"))
                .collect();
            candidates.sort();
            candidates.pop()?
        }
    };
    checkpoint
        .is_file()
        .then_some(DevelopmentDepthModel { checkpoint, source })
}

#[cfg(debug_assertions)]
fn development_path_tool(name: &str) -> Option<PathBuf> {
    let executable = if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_owned()
    };
    let path = env::var_os("PATH")?;
    env::split_paths(&path)
        .map(|directory| directory.join(&executable))
        .find(|candidate| candidate.is_file())
}

#[cfg(debug_assertions)]
fn development_media_tool_pair(
    ffmpeg: &Path,
    ffprobe: &Path,
) -> BridgeResult<DevelopmentMediaTools> {
    let ffmpeg = std::fs::canonicalize(ffmpeg)
        .map_err(|_| BridgeError::worker("configured FFmpeg executable was not found"))?;
    let ffprobe = std::fs::canonicalize(ffprobe)
        .map_err(|_| BridgeError::worker("configured FFprobe executable was not found"))?;
    let tool_directory = ffmpeg
        .parent()
        .filter(|parent| ffprobe.parent() == Some(*parent))
        .ok_or_else(|| BridgeError::worker("development FFmpeg and FFprobe must be colocated"))?
        .to_path_buf();
    let mut search_paths = vec![tool_directory];
    if let Some(existing) = env::var_os("PATH") {
        search_paths.extend(env::split_paths(&existing));
    }
    let path = env::join_paths(search_paths)
        .map_err(|_| BridgeError::worker("could not construct the media-tool PATH"))?;
    Ok(DevelopmentMediaTools {
        ffmpeg,
        ffprobe,
        path,
    })
}

#[cfg(not(debug_assertions))]
struct ReleaseResources {
    ffmpeg: PathBuf,
    ffprobe: PathBuf,
    depth_model: Option<PathBuf>,
    path: OsString,
}

#[cfg(not(debug_assertions))]
fn release_resources(app: &AppHandle) -> BridgeResult<ReleaseResources> {
    let ffmpeg = required_resource_file(app, "tools/ffmpeg.exe", "FFmpeg")?;
    let ffprobe = required_resource_file(app, "tools/ffprobe.exe", "ffprobe")?;
    let tool_directory = ffmpeg
        .parent()
        .filter(|parent| ffprobe.parent() == Some(*parent))
        .ok_or_else(|| BridgeError::worker("packaged media tools are not colocated"))?
        .to_path_buf();

    let mut search_paths = vec![tool_directory];
    if let Some(existing) = env::var_os("PATH") {
        search_paths.extend(env::split_paths(&existing));
    }
    let path = env::join_paths(search_paths)
        .map_err(|_| BridgeError::worker("could not construct the packaged worker PATH"))?;

    Ok(ReleaseResources {
        ffmpeg,
        ffprobe,
        depth_model: verified_depth_model(app)?,
        path,
    })
}

#[cfg(not(debug_assertions))]
fn required_resource_file(
    app: &AppHandle,
    relative: &str,
    label: &'static str,
) -> BridgeResult<PathBuf> {
    let path = app
        .path()
        .resolve(relative, BaseDirectory::Resource)
        .map_err(|_| BridgeError::worker(format!("packaged {label} path is unavailable")))?;
    let path = std::fs::canonicalize(path)
        .map_err(|_| BridgeError::worker(format!("packaged {label} is missing")))?;
    if !path.is_file() {
        return Err(BridgeError::worker(format!(
            "packaged {label} is not a file"
        )));
    }
    Ok(path)
}

#[cfg(not(debug_assertions))]
fn verified_depth_model(app: &AppHandle) -> BridgeResult<Option<PathBuf>> {
    static VERIFIED: OnceLock<Result<Option<PathBuf>, &'static str>> = OnceLock::new();
    VERIFIED
        .get_or_init(|| verify_depth_model_once(app))
        .clone()
        .map_err(BridgeError::worker)
}

#[cfg(not(debug_assertions))]
fn verify_depth_model_once(app: &AppHandle) -> Result<Option<PathBuf>, &'static str> {
    let expected = env!("AISTEREO_BUNDLED_MODEL_SHA256");
    if expected.is_empty() {
        return Ok(None);
    }
    let path = app
        .path()
        .resolve(
            "models/video_depth_anything_small.torchscript",
            BaseDirectory::Resource,
        )
        .map_err(|_| "bundled depth model path is unavailable")?;
    let path = match std::fs::canonicalize(path) {
        Ok(path) if path.is_file() => path,
        _ => return Ok(None),
    };
    let actual = sha256_file(&path).map_err(|_| "bundled depth model could not be verified")?;
    if actual != expected {
        return Err("bundled depth model failed integrity verification");
    }
    Ok(Some(path))
}

#[cfg(not(debug_assertions))]
fn sha256_file(path: &Path) -> std::io::Result<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

#[cfg(debug_assertions)]
fn development_project_root() -> BridgeResult<PathBuf> {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| BridgeError::worker("could not locate the development project root"))
}

fn launch_mode() -> &'static str {
    if cfg!(debug_assertions) {
        "python_module"
    } else {
        "packaged_sidecar"
    }
}

fn worker_start_error_message() -> &'static str {
    if cfg!(debug_assertions) {
        "could not start `python -m aistereo.worker`; install the Python package or set AISTEREO_PYTHON"
    } else {
        "packaged aistereo-worker sidecar is missing or could not be started"
    }
}

fn relay_stdout(app: &AppHandle, bytes: &[u8]) {
    match parse_worker_event(bytes) {
        Ok(event) => emit(app, &event),
        Err(error) => {
            if let Some(id) = request_id_hint(bytes) {
                emit(
                    app,
                    &WorkerEvent::protocol_error(id, "invalid_worker_event", error.message),
                );
            } else {
                emit(
                    app,
                    &WorkerEvent::host_log(
                        LogLevel::Warning,
                        "Python worker emitted an invalid uncorrelated protocol event",
                    ),
                );
            }
        }
    }
}

fn request_id_hint(bytes: &[u8]) -> Option<String> {
    let bounded = &bytes[..bytes.len().min(4096)];
    const NEEDLE: &[u8] = b"\"id\"";
    if bounded.len() < NEEDLE.len() {
        return None;
    }
    for offset in 0..=bounded.len() - NEEDLE.len() {
        if bounded.get(offset..offset + NEEDLE.len()) != Some(NEEDLE) {
            continue;
        }
        let mut cursor = offset + NEEDLE.len();
        while bounded.get(cursor).is_some_and(u8::is_ascii_whitespace) {
            cursor += 1;
        }
        if bounded.get(cursor) != Some(&b':') {
            continue;
        }
        cursor += 1;
        while bounded.get(cursor).is_some_and(u8::is_ascii_whitespace) {
            cursor += 1;
        }
        if bounded.get(cursor) != Some(&b'\"') {
            continue;
        }
        cursor += 1;
        let start = cursor;
        while let Some(byte) = bounded.get(cursor) {
            if *byte == b'\"' {
                let value = std::str::from_utf8(&bounded[start..cursor]).ok()?;
                if validate_app_generated_identifier("event id", value).is_ok() {
                    return Some(value.to_owned());
                }
                break;
            }
            if *byte == b'\\' || byte.is_ascii_control() || cursor - start > 128 {
                break;
            }
            cursor += 1;
        }
    }
    None
}

fn emit(app: &AppHandle, event: &WorkerEvent) {
    if let Err(error) = app.emit(EVENT_CHANNEL, event) {
        eprintln!("failed to emit {EVENT_CHANNEL}: {error}");
    }
}

fn bounded_text(value: &str) -> String {
    if value.len() <= MAX_EVENT_BYTES {
        value.to_owned()
    } else {
        bounded_lossy_text(value.as_bytes(), MAX_EVENT_BYTES)
    }
}

fn bounded_lossy_text(bytes: &[u8], max_bytes: usize) -> String {
    let clipped = &bytes[..bytes.len().min(max_bytes)];
    let mut value = String::from_utf8_lossy(clipped).into_owned();
    if bytes.len() > max_bytes {
        value.push_str(" …[truncated]");
    }
    value
}

fn stderr_diagnostic(bytes: &[u8], exact_secret: Option<&str>) -> String {
    #[cfg(not(debug_assertions))]
    {
        let _ = (bytes, exact_secret);
        "Python worker reported a diagnostic error".to_owned()
    }
    #[cfg(debug_assertions)]
    {
        redact_diagnostic(&bounded_lossy_text(bytes, 16 * 1024), exact_secret)
    }
}

fn redact_diagnostic(value: &str, exact_secret: Option<&str>) -> String {
    #[cfg(not(debug_assertions))]
    {
        let _ = (value, exact_secret);
        "Python worker I/O channel failed".to_owned()
    }
    #[cfg(debug_assertions)]
    {
        let mut redacted = value.to_owned();
        if let Some(secret) = exact_secret.filter(|secret| !secret.is_empty()) {
            redacted = redacted.replace(secret, "[redacted]");
        }
        redacted = redact_bearer_values(redacted);
        redact_sk_values(redacted)
    }
}

#[cfg(debug_assertions)]
fn redact_bearer_values(mut value: String) -> String {
    let mut search_from = 0;
    loop {
        let lowercase = value.to_ascii_lowercase();
        let Some(relative) = lowercase[search_from..].find("bearer ") else {
            break;
        };
        let token_start = search_from + relative + "bearer ".len();
        let token_end = value[token_start..]
            .char_indices()
            .find_map(|(offset, character)| {
                (character.is_whitespace() || matches!(character, '"' | '\'' | ',' | ';'))
                    .then_some(token_start + offset)
            })
            .unwrap_or(value.len());
        if token_end == token_start {
            search_from = token_start;
            continue;
        }
        value.replace_range(token_start..token_end, "[redacted]");
        search_from = token_start + "[redacted]".len();
    }
    value
}

#[cfg(debug_assertions)]
fn redact_sk_values(mut value: String) -> String {
    let mut search_from = 0;
    loop {
        let lowercase = value.to_ascii_lowercase();
        let Some(relative) = lowercase[search_from..].find("sk-") else {
            break;
        };
        let start = search_from + relative;
        let end = value[start..]
            .char_indices()
            .skip(3)
            .find_map(|(offset, character)| {
                (!character.is_ascii_alphanumeric() && !matches!(character, '-' | '_' | '.'))
                    .then_some(start + offset)
            })
            .unwrap_or(value.len());
        value.replace_range(start..end, "[redacted]");
        search_from = start + "[redacted]".len();
    }
    value
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounded_worker_text_is_truncated() {
        let value = vec![b'x'; 20];
        let bounded = bounded_lossy_text(&value, 5);
        assert!(bounded.starts_with("xxxxx"));
        assert!(bounded.ends_with("[truncated]"));
    }

    #[test]
    fn snapshot_starts_stopped() {
        let manager = WorkerManager::default();
        let snapshot = manager.snapshot().expect("snapshot");
        assert!(matches!(snapshot.status, WorkerStatus::Stopped));
        assert!(snapshot.pid.is_none());
    }

    #[cfg(debug_assertions)]
    #[test]
    fn development_media_tools_require_a_colocated_pair() {
        let root = tempfile::tempdir().expect("temporary tool root");
        let bin = root.path().join("bin");
        std::fs::create_dir_all(&bin).expect("tool directory");
        let ffmpeg = bin.join(if cfg!(windows) {
            "ffmpeg.exe"
        } else {
            "ffmpeg"
        });
        let ffprobe = bin.join(if cfg!(windows) {
            "ffprobe.exe"
        } else {
            "ffprobe"
        });
        std::fs::write(&ffmpeg, b"ffmpeg").expect("ffmpeg fixture");
        std::fs::write(&ffprobe, b"ffprobe").expect("ffprobe fixture");

        let tools = development_media_tool_pair(&ffmpeg, &ffprobe).expect("valid tool pair");
        assert!(tools.ffmpeg.is_absolute());
        assert!(tools.ffprobe.is_absolute());

        let other = root.path().join("other");
        std::fs::create_dir_all(&other).expect("second tool directory");
        let moved_probe = other.join(ffprobe.file_name().expect("probe name"));
        std::fs::write(&moved_probe, b"ffprobe").expect("second ffprobe fixture");
        assert!(development_media_tool_pair(&ffmpeg, &moved_probe).is_err());
    }

    #[test]
    fn oversized_event_retains_only_a_valid_request_id_hint() {
        let id = "preview-00000000-0000-4000-8000-000000000001";
        let mut event = format!(r#"{{"type":"result","id":"{id}","result":""#).into_bytes();
        event.resize(MAX_EVENT_BYTES + 1, b'x');

        assert!(parse_worker_event(&event).is_err());
        assert_eq!(request_id_hint(&event).as_deref(), Some(id));
        assert!(request_id_hint(br#"{"id":"sk-proj-not-an-id"}"#).is_none());
    }

    #[cfg(debug_assertions)]
    #[test]
    fn worker_diagnostics_redact_credentials() {
        let secret = "provider_key_1234567890";
        let message = redact_diagnostic(
            "exact provider_key_1234567890; Authorization: Bearer abc.def; key sk-proj-123456",
            Some(secret),
        );
        assert!(!message.contains(secret));
        assert!(!message.contains("abc.def"));
        assert!(!message.contains("sk-proj"));
        assert!(message.matches("[redacted]").count() >= 3);
    }
}
