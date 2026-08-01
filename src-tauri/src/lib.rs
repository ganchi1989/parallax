mod commands;
mod error;
mod paths;
mod protocol;
mod secrets;
mod worker;

use commands::{
    allocate_new_project, authorize_preview_asset, cancel_job, choose_project_folder,
    choose_video_file, create_project, default_project_base, delete_llm_key, has_llm_key,
    llm_key_status, open_project, pick_project_base_directory, pick_project_directory, pick_video,
    plan_new_project, restart_worker, reveal_output, runtime_info, save_llm_key, save_output,
    send_worker_command, stop_worker, worker_request,
};
use paths::PathPolicy;
use secrets::SecretStore;
use tauri::RunEvent;
use worker::WorkerManager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let worker = WorkerManager::default();
    let worker_for_shutdown = worker.clone();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(worker)
        .manage(PathPolicy::default())
        .manage(SecretStore::default())
        .invoke_handler(tauri::generate_handler![
            runtime_info,
            pick_video,
            choose_video_file,
            default_project_base,
            pick_project_base_directory,
            plan_new_project,
            allocate_new_project,
            pick_project_directory,
            choose_project_folder,
            save_output,
            authorize_preview_asset,
            worker_request,
            send_worker_command,
            cancel_job,
            restart_worker,
            stop_worker,
            llm_key_status,
            has_llm_key,
            save_llm_key,
            delete_llm_key,
            create_project,
            open_project,
            reveal_output,
        ])
        .build(tauri::generate_context!())
        .expect("failed to build AI Stereo Director");

    app.run(move |_app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Err(error) = worker_for_shutdown.stop() {
                eprintln!("failed to stop Python worker during shutdown: {error}");
            }
        }
    });
}
