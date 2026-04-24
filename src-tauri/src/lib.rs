use std::net::TcpListener;
use std::sync::Mutex;

use tauri::{Manager, State};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

struct BackendState {
    port: u16,
    child: Mutex<Option<CommandChild>>,
}

#[tauri::command]
fn backend_url(state: State<BackendState>) -> String {
    format!("http://127.0.0.1:{}", state.port)
}

fn choose_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|err| err.to_string())?;
    let port = listener.local_addr().map_err(|err| err.to_string())?.port();
    drop(listener);
    Ok(port)
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let port = choose_port().map_err(|err| format!("Unable to choose API port: {err}"))?;
            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|err| format!("Unable to resolve app data dir: {err}"))?;
            std::fs::create_dir_all(&data_dir)
                .map_err(|err| format!("Unable to create app data dir: {err}"))?;

            let (_rx, child) = app
                .shell()
                .sidecar("paper-engine-api")
                .map_err(|err| format!("Unable to resolve API sidecar: {err}"))?
                .args([
                    "--host",
                    "127.0.0.1",
                    "--port",
                    &port.to_string(),
                    "--data-dir",
                    data_dir.to_str().ok_or("App data dir is not valid UTF-8")?,
                ])
                .spawn()
                .map_err(|err| format!("Unable to start API sidecar: {err}"))?;

            app.manage(BackendState {
                port,
                child: Mutex::new(Some(child)),
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_url])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if let Some(state) = window.try_state::<BackendState>() {
                    if let Ok(mut child) = state.child.lock() {
                        if let Some(process) = child.take() {
                            let _ = process.kill();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}
