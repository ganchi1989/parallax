use crate::error::{BridgeError, BridgeResult};
use crate::paths::{
    normalize_extension, path_to_string, safe_suggested_filename, validate_worker_paths,
    NewProjectPlan, PathPolicy, ProjectFolderKind, DEFAULT_PROJECT_BASE, VIDEO_EXTENSIONS,
};
use crate::protocol::{validate_app_generated_identifier, WorkerAck, WorkerMethod, WorkerRequest};
use crate::secrets::{LlmKeyStatus, SecretStore};
use crate::worker::{WorkerManager, WorkerSnapshot, WorkerStatus};
use serde::Serialize;
use serde_json::{Map, Value};
use std::path::Path;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeInfo {
    mode: &'static str,
    platform: &'static str,
    worker_ready: bool,
    version: &'static str,
    architecture: &'static str,
    debug_build: bool,
    worker: WorkerSnapshot,
    supported_video_extensions: &'static [&'static str],
}

#[tauri::command]
pub fn runtime_info(worker: State<'_, WorkerManager>) -> BridgeResult<RuntimeInfo> {
    let worker = worker.snapshot()?;
    Ok(RuntimeInfo {
        mode: "tauri",
        platform: std::env::consts::OS,
        worker_ready: !matches!(worker.status, WorkerStatus::Faulted),
        version: env!("CARGO_PKG_VERSION"),
        architecture: std::env::consts::ARCH,
        debug_build: cfg!(debug_assertions),
        worker,
        supported_video_extensions: VIDEO_EXTENSIONS,
    })
}

#[tauri::command]
pub fn default_project_base() -> String {
    DEFAULT_PROJECT_BASE.to_owned()
}

#[tauri::command]
pub async fn pick_video(
    app: AppHandle,
    paths: State<'_, PathPolicy>,
) -> BridgeResult<Option<String>> {
    let selection = app
        .dialog()
        .file()
        .set_title("Choose a source video")
        .add_filter("Video files", VIDEO_EXTENSIONS)
        .blocking_pick_file();
    let Some(selection) = selection else {
        return Ok(None);
    };
    let path = selection
        .into_path()
        .map_err(|_| BridgeError::path("only local video files are supported"))?;
    let path = paths.approve_video(&path)?;
    allow_asset_file(&app, &path)?;
    Ok(Some(path_to_string(&path)?))
}

#[tauri::command]
pub async fn choose_video_file(
    app: AppHandle,
    paths: State<'_, PathPolicy>,
) -> BridgeResult<Option<String>> {
    pick_video(app, paths).await
}

#[tauri::command]
pub async fn pick_project_base_directory(
    app: AppHandle,
    paths: State<'_, PathPolicy>,
) -> BridgeResult<Option<String>> {
    let selection = app
        .dialog()
        .file()
        .set_title("Choose where new projects are stored")
        .blocking_pick_folder();
    let Some(selection) = selection else {
        return Ok(None);
    };
    let path = selection
        .into_path()
        .map_err(|_| BridgeError::path("only local project locations are supported"))?;
    let path = paths.approve_project_base(&path)?;
    Ok(Some(path_to_string(&path)?))
}

#[tauri::command]
pub async fn plan_new_project(
    app: AppHandle,
    source_video: String,
    base_directory: Option<String>,
) -> BridgeResult<NewProjectPlan> {
    tauri::async_runtime::spawn_blocking(move || {
        let paths = app.state::<PathPolicy>();
        paths.plan_new_project(
            Path::new(&source_video),
            base_directory.as_deref().map(Path::new),
        )
    })
    .await
    .map_err(|_| BridgeError::internal("project planning task could not be completed"))?
}

#[tauri::command]
pub async fn allocate_new_project(
    app: AppHandle,
    source_video: String,
    base_directory: Option<String>,
) -> BridgeResult<NewProjectPlan> {
    tauri::async_runtime::spawn_blocking(move || {
        let paths = app.state::<PathPolicy>();
        paths.allocate_new_project(
            Path::new(&source_video),
            base_directory.as_deref().map(Path::new),
        )
    })
    .await
    .map_err(|_| BridgeError::internal("project allocation task could not be completed"))?
}

#[tauri::command]
pub async fn pick_project_directory(
    app: AppHandle,
    paths: State<'_, PathPolicy>,
) -> BridgeResult<Option<String>> {
    let selection = app
        .dialog()
        .file()
        .set_title("Choose a project folder")
        .blocking_pick_folder();
    let Some(selection) = selection else {
        return Ok(None);
    };
    let path = selection
        .into_path()
        .map_err(|_| BridgeError::path("only local project folders are supported"))?;
    let inspection = paths.inspect_project_folder(&path)?;
    let path = match inspection.kind {
        ProjectFolderKind::Empty => paths.approve_root(&inspection.path)?,
        ProjectFolderKind::Existing => {
            let source_selection = app
                .dialog()
                .file()
                .set_title("Confirm this project's source video")
                .add_filter("Video files", VIDEO_EXTENSIONS)
                .blocking_pick_file();
            let Some(source_selection) = source_selection else {
                return Ok(None);
            };
            let source_path = source_selection
                .into_path()
                .map_err(|_| BridgeError::path("only local video files are supported"))?;
            paths.approve_existing_project(&inspection.path, &source_path)?
        }
    };
    Ok(Some(path_to_string(&path)?))
}

#[tauri::command]
pub async fn choose_project_folder(
    app: AppHandle,
    paths: State<'_, PathPolicy>,
) -> BridgeResult<Option<String>> {
    pick_project_directory(app, paths).await
}

#[tauri::command]
pub async fn save_output(
    app: AppHandle,
    paths: State<'_, PathPolicy>,
    suggested_name: String,
    extension: String,
) -> BridgeResult<Option<String>> {
    let extension = normalize_extension(&extension)?;
    let suggested_name = safe_suggested_filename(&suggested_name, &extension)?;
    let selection = app
        .dialog()
        .file()
        .set_title("Save rendered video")
        .set_file_name(&suggested_name)
        .add_filter("Video output", &[extension.as_str()])
        .blocking_save_file();
    let Some(selection) = selection else {
        return Ok(None);
    };
    let mut path = selection
        .into_path()
        .map_err(|_| BridgeError::path("only local output files are supported"))?;
    if path.extension().is_none() {
        path.set_extension(&extension);
    }
    let path = paths.approve_output(&path, &extension)?;
    allow_asset_file(&app, &path)?;
    Ok(Some(path_to_string(&path)?))
}

#[tauri::command]
pub fn worker_request(
    app: AppHandle,
    worker: State<'_, WorkerManager>,
    paths: State<'_, PathPolicy>,
    mut request: WorkerRequest,
) -> BridgeResult<WorkerAck> {
    request.validate()?;
    validate_worker_paths(request.method, &mut request.params, &paths)?;
    worker.send(&app, &request)
}

#[tauri::command]
pub fn send_worker_command(
    app: AppHandle,
    worker: State<'_, WorkerManager>,
    paths: State<'_, PathPolicy>,
    request: WorkerRequest,
) -> BridgeResult<WorkerAck> {
    worker_request(app, worker, paths, request)
}

#[tauri::command]
pub fn cancel_job(
    app: AppHandle,
    worker: State<'_, WorkerManager>,
    job_id: String,
) -> BridgeResult<WorkerAck> {
    validate_app_generated_identifier("job id", &job_id)?;
    let mut params = Map::new();
    params.insert("job_id".to_owned(), Value::String(job_id.clone()));
    let request = WorkerRequest {
        id: format!("cancel-{}", uuid::Uuid::new_v4()),
        method: WorkerMethod::Cancel,
        params,
    };
    worker.send(&app, &request)
}

#[tauri::command]
pub fn restart_worker(
    app: AppHandle,
    worker: State<'_, WorkerManager>,
) -> BridgeResult<WorkerSnapshot> {
    worker.restart(&app)
}

#[tauri::command]
pub fn stop_worker(worker: State<'_, WorkerManager>) -> BridgeResult<bool> {
    worker.stop()
}

#[tauri::command]
pub fn llm_key_status(
    secrets: State<'_, SecretStore>,
    worker: State<'_, WorkerManager>,
) -> BridgeResult<LlmKeyStatus> {
    let worker_running = worker.snapshot()?.pid.is_some();
    secrets.status(worker_running)
}

#[tauri::command]
pub fn has_llm_key(secrets: State<'_, SecretStore>) -> BridgeResult<bool> {
    Ok(secrets.status(false)?.configured)
}

#[tauri::command]
pub fn save_llm_key(
    secrets: State<'_, SecretStore>,
    worker: State<'_, WorkerManager>,
    api_key: String,
) -> BridgeResult<LlmKeyStatus> {
    secrets.save(api_key, false)?;
    // Always take the lifecycle lock and stop after the write. This closes the
    // race where a request could start a child between a status snapshot and
    // credential replacement. The next request lazily inherits the new key.
    worker.stop()?;
    secrets.status(false)
}

#[tauri::command]
pub fn delete_llm_key(
    secrets: State<'_, SecretStore>,
    worker: State<'_, WorkerManager>,
) -> BridgeResult<LlmKeyStatus> {
    secrets.delete()?;
    // A running child retains its inherited environment. Stop it immediately so
    // deletion also removes the key from all app-owned processes.
    worker.stop()?;
    secrets.status(false)
}

#[tauri::command]
pub fn create_project(
    app: AppHandle,
    worker: State<'_, WorkerManager>,
    paths: State<'_, PathPolicy>,
    id: String,
    project_directory: String,
    source_video: String,
) -> BridgeResult<WorkerAck> {
    let mut params = Map::new();
    params.insert("project_dir".to_owned(), Value::String(project_directory));
    params.insert("input_path".to_owned(), Value::String(source_video));
    let mut request = WorkerRequest {
        id,
        method: WorkerMethod::CreateProject,
        params,
    };
    request.validate()?;
    validate_worker_paths(request.method, &mut request.params, &paths)?;
    worker.send(&app, &request)
}

#[tauri::command]
pub fn open_project(
    app: AppHandle,
    worker: State<'_, WorkerManager>,
    paths: State<'_, PathPolicy>,
    id: String,
    project_directory: String,
) -> BridgeResult<WorkerAck> {
    let mut params = Map::new();
    params.insert("project_dir".to_owned(), Value::String(project_directory));
    let mut request = WorkerRequest {
        id,
        method: WorkerMethod::GetProject,
        params,
    };
    request.validate()?;
    validate_worker_paths(request.method, &mut request.params, &paths)?;
    worker.send(&app, &request)
}

#[tauri::command]
pub fn reveal_output(
    app: AppHandle,
    paths: State<'_, PathPolicy>,
    path: String,
) -> BridgeResult<()> {
    let path = paths.resolve_allowed_existing_any(Path::new(&path))?;
    app.opener()
        .reveal_item_in_dir(&path)
        .map_err(|_| BridgeError::internal("could not reveal the output in File Explorer"))
}

#[tauri::command]
pub fn authorize_preview_asset(
    app: AppHandle,
    paths: State<'_, PathPolicy>,
    path: String,
) -> BridgeResult<String> {
    let path = paths.resolve_allowed_preview_file(Path::new(&path))?;
    allow_asset_file(&app, &path)?;
    path_to_string(&path)
}

fn allow_asset_file(app: &AppHandle, path: &Path) -> BridgeResult<()> {
    app.state::<tauri::scope::Scopes>()
        .allow_file(path)
        .map_err(|_| BridgeError::internal("could not grant preview access to the selected file"))
}
