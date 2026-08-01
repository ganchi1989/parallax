fn main() {
    println!("cargo:rerun-if-env-changed=TAURI_CONFIG");
    let model_hash_path = std::path::Path::new("resources/models/DEPTH-MODEL.sha256");
    println!("cargo:rerun-if-changed={}", model_hash_path.display());
    let model_hash = match std::fs::read_to_string(model_hash_path) {
        Ok(value) => {
            let value = value.trim();
            if value.len() != 64
                || !value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            {
                panic!("resources/models/DEPTH-MODEL.sha256 must contain one lowercase SHA-256");
            }
            value.to_owned()
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(_) => panic!("could not read the reviewed depth-model hash"),
    };
    println!("cargo:rustc-env=AISTEREO_BUNDLED_MODEL_SHA256={model_hash}");

    // `externalBin` and packaged resources are release staging contracts.
    // Developer checks and `tauri dev` launch the Python module and must work
    // from a clean checkout. Merge into a Tauri CLI-provided override instead
    // of assuming the variable is absent.
    if std::env::var("PROFILE").as_deref() == Ok("debug") {
        let mut config = match std::env::var("TAURI_CONFIG") {
            Ok(value) => serde_json::from_str::<serde_json::Value>(&value)
                .expect("TAURI_CONFIG must contain a JSON object"),
            Err(std::env::VarError::NotPresent) => serde_json::json!({}),
            Err(std::env::VarError::NotUnicode(_)) => {
                panic!("TAURI_CONFIG must be valid Unicode JSON")
            }
        };
        let root = config
            .as_object_mut()
            .expect("TAURI_CONFIG must contain a JSON object");
        let bundle = root
            .entry("bundle")
            .or_insert_with(|| serde_json::json!({}))
            .as_object_mut()
            .expect("TAURI_CONFIG bundle override must be an object");
        bundle.insert("externalBin".to_owned(), serde_json::json!([]));
        bundle.insert("resources".to_owned(), serde_json::json!([]));
        std::env::set_var(
            "TAURI_CONFIG",
            serde_json::to_string(&config).expect("debug Tauri config must serialize"),
        );
    }
    tauri_build::build()
}
