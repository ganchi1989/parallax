use crate::error::{BridgeError, BridgeResult};
use serde::Serialize;
use std::env;
use std::sync::{Mutex, MutexGuard};
use zeroize::Zeroizing;

const SERVICE_NAME: &str = "com.parallaxforge.desktop";
const ACCOUNT_NAME: &str = "llm-api-key";
pub const WORKER_KEY_ENV: &str = "AISTEREO_LLM_API_KEY";

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SecretSource {
    CredentialStore,
    Environment,
    None,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmKeyStatus {
    pub configured: bool,
    pub source: SecretSource,
    pub secure_persistent_storage: bool,
    pub worker_restart_required: bool,
}

/// Serializes access to the native store. In particular, the Windows
/// credential backend does not guarantee ordering for concurrent operations on
/// the same entry.
#[derive(Debug, Default)]
pub struct SecretStore {
    gate: Mutex<()>,
}

impl SecretStore {
    pub fn status(&self, worker_restart_required: bool) -> BridgeResult<LlmKeyStatus> {
        let (_key, source) = self.load_with_source()?;
        let configured = !matches!(source, SecretSource::None);
        Ok(LlmKeyStatus {
            configured,
            source,
            secure_persistent_storage: cfg!(windows),
            worker_restart_required: configured && worker_restart_required,
        })
    }

    pub fn save(&self, key: String, worker_restart_required: bool) -> BridgeResult<LlmKeyStatus> {
        let key = Zeroizing::new(key);
        validate_key(key.as_str())?;
        let _gate = self.lock()?;

        #[cfg(windows)]
        {
            credential_entry()?
                .set_password(key.as_str())
                .map_err(|_| secure_store_error("could not save the LLM key"))?;
            Ok(LlmKeyStatus {
                configured: true,
                source: SecretSource::CredentialStore,
                secure_persistent_storage: true,
                worker_restart_required,
            })
        }

        #[cfg(not(windows))]
        {
            let _ = worker_restart_required;
            Err(BridgeError::new(
                "secure_storage_unavailable",
                "persistent LLM key storage is not enabled on this platform; set AISTEREO_LLM_API_KEY before launching the app",
            ))
        }
    }

    pub fn delete(&self) -> BridgeResult<bool> {
        let _gate = self.lock()?;

        #[cfg(windows)]
        {
            match credential_entry()?.delete_credential() {
                Ok(()) => Ok(true),
                Err(keyring::v1::Error::NoEntry) => Ok(false),
                Err(_) => Err(secure_store_error("could not delete the saved LLM key")),
            }
        }

        #[cfg(not(windows))]
        {
            Ok(false)
        }
    }

    /// Returns a zeroizing copy exclusively for construction of the worker's
    /// environment. This method is never exposed as a Tauri command.
    pub fn load_for_worker(&self) -> BridgeResult<Option<Zeroizing<String>>> {
        self.load_with_source().map(|(key, _source)| key)
    }

    fn load_with_source(&self) -> BridgeResult<(Option<Zeroizing<String>>, SecretSource)> {
        let _gate = self.lock()?;

        #[cfg(windows)]
        {
            match credential_entry()?.get_password() {
                Ok(key) => {
                    let key = Zeroizing::new(key);
                    validate_key(key.as_str())?;
                    return Ok((Some(key), SecretSource::CredentialStore));
                }
                Err(keyring::v1::Error::NoEntry) => {}
                Err(_) => return Err(secure_store_error("could not read LLM key status")),
            }
        }

        match env::var(WORKER_KEY_ENV) {
            Ok(key) => {
                let key = Zeroizing::new(key);
                validate_key(key.as_str())?;
                Ok((Some(key), SecretSource::Environment))
            }
            Err(env::VarError::NotPresent) => Ok((None, SecretSource::None)),
            Err(env::VarError::NotUnicode(_)) => Err(BridgeError::new(
                "invalid_environment",
                "AISTEREO_LLM_API_KEY is not valid Unicode",
            )),
        }
    }

    fn lock(&self) -> BridgeResult<MutexGuard<'_, ()>> {
        self.gate
            .lock()
            .map_err(|_| BridgeError::internal("secret store lock was poisoned"))
    }
}

#[cfg(windows)]
fn credential_entry() -> BridgeResult<keyring::v1::Entry> {
    keyring::v1::Entry::new(SERVICE_NAME, ACCOUNT_NAME)
        .map_err(|_| secure_store_error("Windows Credential Manager is unavailable"))
}

fn secure_store_error(message: &'static str) -> BridgeError {
    // Credential backend diagnostics are intentionally not forwarded because
    // they may contain account or target metadata.
    BridgeError::new("secure_storage_error", message)
}

fn validate_key(key: &str) -> BridgeResult<()> {
    if key.len() < 16 || key.len() > 1024 {
        return Err(BridgeError::invalid(
            "LLM key must contain between 16 and 1024 bytes",
        ));
    }
    if key
        .chars()
        .any(|value| value.is_control() || value.is_whitespace())
    {
        return Err(BridgeError::invalid(
            "LLM key must not contain whitespace or control characters",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_key_shape_without_assuming_a_provider_prefix() {
        assert!(validate_key("provider_key_1234567890").is_ok());
        assert!(validate_key("too-short").is_err());
        assert!(validate_key("provider key with spaces").is_err());
    }
}
