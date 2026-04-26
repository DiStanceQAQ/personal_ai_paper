use std::net::{TcpListener, TcpStream};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs(60);
const STARTUP_TRACE_ENV: &str = "PAPER_ENGINE_STARTUP_TRACE";

struct BackendState {
    port: u16,
    child: Mutex<Option<CommandChild>>,
    startup_started_at: Instant,
}

#[tauri::command]
async fn backend_url(state: State<'_, BackendState>) -> Result<String, String> {
    let port = state.port;
    let startup_started_at = state.startup_started_at;
    startup_trace(startup_started_at, "backend_wait_start", &format!("port={port}"));

    let wait_started_at = Instant::now();
    let wait_result =
        tauri::async_runtime::spawn_blocking(move || wait_for_backend(port, BACKEND_STARTUP_TIMEOUT))
            .await
            .map_err(|err| format!("Unable to wait for API sidecar: {err}"))?;

    if let Err(error) = wait_result {
        startup_trace(
            startup_started_at,
            "backend_wait_failed",
            &format!("port={port} wait_ms={} error={error}", wait_started_at.elapsed().as_millis()),
        );
        return Err(error);
    }

    startup_trace(
        startup_started_at,
        "backend_ready",
        &format!("port={port} wait_ms={}", wait_started_at.elapsed().as_millis()),
    );

    Ok(format_backend_url(port))
}

fn format_backend_url(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

fn format_startup_trace(component: &str, event: &str, elapsed: Duration, details: &str) -> String {
    let suffix = if details.is_empty() {
        String::new()
    } else {
        format!(" {details}")
    };
    format!(
        "[paper-engine startup] {component} event={event} elapsed_ms={}{}",
        elapsed.as_millis(),
        suffix
    )
}

fn startup_trace(started_at: Instant, event: &str, details: &str) {
    eprintln!(
        "{}",
        format_startup_trace("tauri", event, started_at.elapsed(), details)
    );
}

fn choose_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|err| err.to_string())?;
    let port = listener.local_addr().map_err(|err| err.to_string())?.port();
    drop(listener);
    Ok(port)
}

fn wait_for_backend(port: u16, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;

    loop {
        match TcpStream::connect(("127.0.0.1", port)) {
            Ok(_) => return Ok(()),
            Err(error) => {
                if Instant::now() >= deadline {
                    return Err(format!(
                        "Timed out waiting for API sidecar on 127.0.0.1:{port}: {}",
                        error
                    ));
                }
                thread::sleep(Duration::from_millis(50));
            }
        }
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let startup_started_at = Instant::now();
            startup_trace(startup_started_at, "setup_start", "");

            let port = choose_port().map_err(|err| format!("Unable to choose API port: {err}"))?;
            startup_trace(startup_started_at, "port_selected", &format!("port={port}"));

            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|err| format!("Unable to resolve app data dir: {err}"))?;
            std::fs::create_dir_all(&data_dir)
                .map_err(|err| format!("Unable to create app data dir: {err}"))?;
            startup_trace(
                startup_started_at,
                "app_data_ready",
                &format!("data_dir={}", data_dir.display()),
            );

            let sidecar_spawn_started_at = Instant::now();
            startup_trace(startup_started_at, "sidecar_spawn_start", &format!("port={port}"));
            let (mut rx, child) = app
                .shell()
                .sidecar("paper-engine-api")
                .map_err(|err| format!("Unable to resolve API sidecar: {err}"))?
                .env(STARTUP_TRACE_ENV, "1")
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
            startup_trace(
                startup_started_at,
                "sidecar_spawned",
                &format!(
                    "port={port} pid={} spawn_ms={}",
                    child.pid(),
                    sidecar_spawn_started_at.elapsed().as_millis()
                ),
            );

            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            eprint!("{}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Stderr(line) => {
                            eprint!("{}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Error(error) => {
                            eprintln!("API sidecar event error: {error}");
                        }
                        CommandEvent::Terminated(payload) => {
                            eprintln!(
                                "API sidecar terminated with code {:?} signal {:?}",
                                payload.code, payload.signal
                            );
                        }
                        _ => {}
                    }
                }
            });

            app.manage(BackendState {
                port,
                child: Mutex::new(Some(child)),
                startup_started_at,
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::thread;
    use std::time::Duration;

    #[test]
    fn format_backend_url_uses_loopback_with_port() {
        assert_eq!(format_backend_url(8765), "http://127.0.0.1:8765");
    }

    #[test]
    fn format_startup_trace_writes_structured_timing() {
        let line = format_startup_trace(
            "tauri",
            "sidecar_spawned",
            Duration::from_millis(42),
            "port=8765 pid=123",
        );

        assert_eq!(
            line,
            "[paper-engine startup] tauri event=sidecar_spawned elapsed_ms=42 port=8765 pid=123"
        );
    }

    #[test]
    fn backend_startup_timeout_allows_slow_onefile_cold_start() {
        assert!(BACKEND_STARTUP_TIMEOUT >= Duration::from_secs(30));
    }

    #[test]
    fn wait_for_backend_succeeds_when_port_starts_listening() {
        let probe = TcpListener::bind("127.0.0.1:0").expect("bind probe port");
        let port = probe.local_addr().expect("probe local addr").port();
        drop(probe);

        thread::spawn(move || {
            thread::sleep(Duration::from_millis(50));
            let listener = TcpListener::bind(("127.0.0.1", port)).expect("bind delayed backend");
            let _ = listener.accept();
        });

        wait_for_backend(port, Duration::from_secs(2)).expect("backend should become ready");
    }

    #[test]
    fn wait_for_backend_times_out_when_port_stays_closed() {
        let probe = TcpListener::bind("127.0.0.1:0").expect("bind probe port");
        let port = probe.local_addr().expect("probe local addr").port();
        drop(probe);

        let error = wait_for_backend(port, Duration::from_millis(25)).expect_err("closed port should time out");

        assert!(error.contains("Timed out waiting for API sidecar"));
    }
}
