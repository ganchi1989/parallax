use serde::Serialize;
use serde_json::Value;
use std::fmt::{Display, Formatter};

pub type BridgeResult<T> = Result<T, BridgeError>;

/// A stable, serializable error returned across the Tauri invoke boundary.
///
/// Internal Rust error chains are deliberately not exposed to the webview.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BridgeError {
    pub code: &'static str,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<Value>,
}

impl BridgeError {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            details: None,
        }
    }

    pub fn invalid(message: impl Into<String>) -> Self {
        Self::new("invalid_request", message)
    }

    pub fn path(message: impl Into<String>) -> Self {
        Self::new("invalid_path", message)
    }

    pub fn worker(message: impl Into<String>) -> Self {
        Self::new("worker_error", message)
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::new("internal_error", message)
    }
}

impl Display for BridgeError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for BridgeError {}
