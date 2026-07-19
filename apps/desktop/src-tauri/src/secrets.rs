//! Encrypted secret vault for the installation token and BYOK API keys.
//!
//! Secrets are encrypted with a random AES-256-GCM key. That key is itself
//! protected by Windows DPAPI (`CryptProtectData`), so it can only be unwrapped
//! by the same Windows user on the same machine. Nothing is ever written in
//! plaintext, and secrets never appear in JSON config, logs, or the command line.
//!
//! On-disk layout, all inside `<app_local_data>/secrets/`:
//! * `dpapi_key.bin` — the AES key, DPAPI-wrapped.
//! * `vault.bin`     — `nonce (12 bytes) || AES-GCM ciphertext` of the JSON map.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use aes_gcm::aead::{Aead, KeyInit};
use aes_gcm::{Aes256Gcm, Key, Nonce};
use zeroize::Zeroize;

const KEY_FILE: &str = "dpapi_key.bin";
const VAULT_FILE: &str = "vault.bin";
const NONCE_LEN: usize = 12;
const KEY_LEN: usize = 32;

pub type SecretMap = BTreeMap<String, String>;

fn secrets_dir(base: &Path) -> PathBuf {
    base.join("secrets")
}

/// Persist a single secret under `key`, merging into the existing vault.
pub fn set_secret(base: &Path, key: &str, value: &str) -> Result<(), String> {
    let dir = secrets_dir(base);
    fs::create_dir_all(&dir).map_err(|e| format!("could not create secrets directory: {e}"))?;
    let aes_key = load_or_create_key(&dir)?;
    let mut map = read_vault(&dir, &aes_key)?;
    map.insert(key.to_string(), value.to_string());
    let result = write_vault(&dir, &aes_key, &map);
    map.values_mut().for_each(|v| v.zeroize());
    result
}

/// Read a single secret, or `None` if the vault or key is absent.
pub fn get_secret(base: &Path, key: &str) -> Result<Option<String>, String> {
    let dir = secrets_dir(base);
    if !dir.join(VAULT_FILE).exists() || !dir.join(KEY_FILE).exists() {
        return Ok(None);
    }
    let aes_key = load_or_create_key(&dir)?;
    let mut map = read_vault(&dir, &aes_key)?;
    let value = map.remove(key);
    map.values_mut().for_each(|v| v.zeroize());
    Ok(value)
}

/// Remove the named keys. Missing keys are ignored. Writing an empty map still
/// leaves an (encrypted, empty) vault behind rather than deleting the file.
pub fn remove_secrets(base: &Path, keys: &[&str]) -> Result<(), String> {
    let dir = secrets_dir(base);
    if !dir.join(VAULT_FILE).exists() {
        return Ok(());
    }
    let aes_key = load_or_create_key(&dir)?;
    let mut map = read_vault(&dir, &aes_key)?;
    for key in keys {
        map.remove(*key);
    }
    let result = write_vault(&dir, &aes_key, &map);
    map.values_mut().for_each(|v| v.zeroize());
    result
}

fn load_or_create_key(dir: &Path) -> Result<[u8; KEY_LEN], String> {
    let key_path = dir.join(KEY_FILE);
    if key_path.exists() {
        let wrapped = fs::read(&key_path).map_err(|e| format!("could not read key file: {e}"))?;
        let mut unwrapped = dpapi_unprotect(&wrapped)?;
        if unwrapped.len() != KEY_LEN {
            unwrapped.zeroize();
            return Err("stored vault key is corrupt".into());
        }
        let mut key = [0u8; KEY_LEN];
        key.copy_from_slice(&unwrapped);
        unwrapped.zeroize();
        return Ok(key);
    }
    let mut key = [0u8; KEY_LEN];
    getrandom::getrandom(&mut key).map_err(|e| format!("could not generate vault key: {e}"))?;
    let wrapped = dpapi_protect(&key)?;
    // Write the wrapped key atomically so a crash never leaves a half-written key.
    write_atomic(&key_path, &wrapped)?;
    Ok(key)
}

fn read_vault(dir: &Path, aes_key: &[u8; KEY_LEN]) -> Result<SecretMap, String> {
    let vault_path = dir.join(VAULT_FILE);
    if !vault_path.exists() {
        return Ok(SecretMap::new());
    }
    let blob = fs::read(&vault_path).map_err(|e| format!("could not read vault: {e}"))?;
    if blob.len() < NONCE_LEN {
        return Err("vault file is corrupt".into());
    }
    let (nonce_bytes, ciphertext) = blob.split_at(NONCE_LEN);
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(aes_key));
    let mut plaintext = cipher
        .decrypt(Nonce::from_slice(nonce_bytes), ciphertext)
        .map_err(|_| "could not decrypt vault".to_string())?;
    let map: SecretMap = serde_json::from_slice(&plaintext)
        .map_err(|e| format!("could not parse vault contents: {e}"))?;
    plaintext.zeroize();
    Ok(map)
}

fn write_vault(dir: &Path, aes_key: &[u8; KEY_LEN], map: &SecretMap) -> Result<(), String> {
    let mut plaintext =
        serde_json::to_vec(map).map_err(|e| format!("could not serialize vault: {e}"))?;
    let mut nonce_bytes = [0u8; NONCE_LEN];
    getrandom::getrandom(&mut nonce_bytes).map_err(|e| format!("could not generate nonce: {e}"))?;
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(aes_key));
    let ciphertext = cipher
        .encrypt(Nonce::from_slice(&nonce_bytes), plaintext.as_slice())
        .map_err(|_| "could not encrypt vault".to_string())?;
    plaintext.zeroize();
    let mut blob = Vec::with_capacity(NONCE_LEN + ciphertext.len());
    blob.extend_from_slice(&nonce_bytes);
    blob.extend_from_slice(&ciphertext);
    write_atomic(&dir.join(VAULT_FILE), &blob)
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, bytes).map_err(|e| format!("could not write {}: {e}", path.display()))?;
    fs::rename(&tmp, path).map_err(|e| format!("could not commit {}: {e}", path.display()))
}

// --- DPAPI (Windows) ---

#[cfg(windows)]
fn dpapi_protect(plaintext: &[u8]) -> Result<Vec<u8>, String> {
    use std::ptr;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{CryptProtectData, CRYPT_INTEGER_BLOB};
    unsafe {
        let input = CRYPT_INTEGER_BLOB {
            cbData: plaintext.len() as u32,
            pbData: plaintext.as_ptr() as *mut u8,
        };
        let mut output = CRYPT_INTEGER_BLOB {
            cbData: 0,
            pbData: ptr::null_mut(),
        };
        let ok = CryptProtectData(
            &input,
            ptr::null(),
            ptr::null(),
            ptr::null(),
            ptr::null(),
            0,
            &mut output,
        );
        if ok == 0 {
            return Err("DPAPI CryptProtectData failed".into());
        }
        let wrapped =
            std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        LocalFree(output.pbData as _);
        Ok(wrapped)
    }
}

#[cfg(windows)]
fn dpapi_unprotect(wrapped: &[u8]) -> Result<Vec<u8>, String> {
    use std::ptr;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{CryptUnprotectData, CRYPT_INTEGER_BLOB};
    unsafe {
        let input = CRYPT_INTEGER_BLOB {
            cbData: wrapped.len() as u32,
            pbData: wrapped.as_ptr() as *mut u8,
        };
        let mut output = CRYPT_INTEGER_BLOB {
            cbData: 0,
            pbData: ptr::null_mut(),
        };
        let ok = CryptUnprotectData(
            &input,
            ptr::null_mut(),
            ptr::null(),
            ptr::null(),
            ptr::null(),
            0,
            &mut output,
        );
        if ok == 0 {
            return Err("DPAPI CryptUnprotectData failed".into());
        }
        let plaintext =
            std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        LocalFree(output.pbData as _);
        Ok(plaintext)
    }
}

#[cfg(not(windows))]
fn dpapi_protect(_plaintext: &[u8]) -> Result<Vec<u8>, String> {
    Err("Secret storage requires Windows DPAPI.".into())
}

#[cfg(not(windows))]
fn dpapi_unprotect(_wrapped: &[u8]) -> Result<Vec<u8>, String> {
    Err("Secret storage requires Windows DPAPI.".into())
}
