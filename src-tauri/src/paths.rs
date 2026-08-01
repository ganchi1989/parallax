use crate::error::{BridgeError, BridgeResult};
use crate::protocol::WorkerMethod;
use serde::Serialize;
use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::ErrorKind;
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

pub const VIDEO_EXTENSIONS: &[&str] = &["mp4", "mov", "mkv", "m4v", "avi", "webm", "mpg", "mpeg"];
pub const OUTPUT_EXTENSIONS: &[&str] = &["mp4", "mkv", "mov"];
pub const DEFAULT_PROJECT_BASE: &str = r"D:\Parallax Projects";
const CONFIG_EXTENSIONS: &[&str] = &["json", "yaml", "yml", "toml"];
const PREVIEW_EXTENSIONS: &[&str] = &[
    "mp4", "mov", "mkv", "m4v", "avi", "webm", "mpg", "mpeg", "png", "jpg", "jpeg", "webp",
];
const MAX_PROJECT_MARKER_BYTES: u64 = 256 * 1024;
const MAX_DEPTH_MANIFEST_BYTES: u64 = 8 * 1024 * 1024;
const MAX_PROJECT_NAME_UTF16: usize = 96;
const MAX_PROJECT_COLLISION_INDEX: usize = 10_000;

const PROJECT_DIRECTORIES: &[&str] = &[
    "source", "shots", "depth", "features", "director", "previews", "renders", "logs", "qc",
];

const FIXED_PROJECT_FILES: &[&str] = &[
    "config.json",
    "pipeline_state.json",
    "source/media.json",
    "source/normalized_media.json",
    "source/normalized.mp4",
    "source/audio.mka",
    "shots/shots.json",
    "depth/metadata.json",
    "features/features.json",
    "director/stereo_script.json",
    "director/applied_stereo_script.json",
    "qc/report.json",
    "qc/report.html",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProjectFolderKind {
    Empty,
    Existing,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectFolderInspection {
    pub path: PathBuf,
    pub kind: ProjectFolderKind,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NewProjectPlan {
    pub source_path: String,
    pub source_name: String,
    pub base_directory: String,
    pub project_directory: String,
    pub project_name: String,
    pub folder_name: String,
    pub collision_index: usize,
    pub created: bool,
}

#[derive(Debug)]
struct ProjectMarker {
    input_path: String,
}

#[derive(Debug, Default)]
struct ApprovedPaths {
    roots: HashSet<PathBuf>,
    project_bases: HashSet<PathBuf>,
    files: HashSet<PathBuf>,
    output_files: HashSet<PathBuf>,
    project_sources: HashMap<PathBuf, PathBuf>,
}

/// Paths become accessible only after a native picker has granted them. A root
/// grant covers project-generated artifacts below that root; a video grant is
/// file-specific.
#[derive(Debug, Default)]
pub struct PathPolicy {
    inner: Mutex<ApprovedPaths>,
}

impl PathPolicy {
    pub fn approve_video(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = canonical_existing_file(path)?;
        ensure_extension(&path, VIDEO_EXTENSIONS, "video")?;
        self.lock()?.files.insert(path.clone());
        Ok(path)
    }

    /// Records a storage location selected by the native folder picker.
    ///
    /// Project bases are deliberately separate from project roots: selecting a
    /// base never grants the worker recursive access to existing sibling data.
    pub fn approve_project_base(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = canonical_existing_directory(path)?;
        self.lock()?.project_bases.insert(path.clone());
        Ok(path)
    }

    pub fn plan_new_project(
        &self,
        source_video: &Path,
        base_directory: Option<&Path>,
    ) -> BridgeResult<NewProjectPlan> {
        let source = self.resolve_allowed_video(source_video)?;
        let base = self.resolve_project_base(base_directory, false)?;
        let display_base = if base_directory.is_none() {
            PathBuf::from(DEFAULT_PROJECT_BASE)
        } else {
            base.clone()
        };
        plan_new_project_at_bases(&source, &base, &display_base)
    }

    pub fn allocate_new_project(
        &self,
        source_video: &Path,
        base_directory: Option<&Path>,
    ) -> BridgeResult<NewProjectPlan> {
        let source = self.resolve_allowed_video(source_video)?;
        let base = self.resolve_project_base(base_directory, true)?;
        let display_base = if base_directory.is_none() {
            PathBuf::from(DEFAULT_PROJECT_BASE)
        } else {
            base.clone()
        };
        let project_name = safe_project_name(&source)?;
        let (project, folder_name, collision_index) =
            create_unique_project_directory(&base, &project_name)?;
        let plan = new_project_plan(&source, &display_base, folder_name, collision_index, true)?;

        // Commit the exact child and its already picker-approved source
        // together. The base remains storage-only and receives no recursive
        // worker or asset-protocol grant.
        let mut approved = self.lock()?;
        approved.roots.insert(project.clone());
        approved.files.insert(source.clone());
        approved.project_sources.insert(project, source);
        Ok(plan)
    }

    pub fn approve_root(&self, path: &Path) -> BridgeResult<PathBuf> {
        let inspection = inspect_project_folder(path)?;
        if inspection.kind != ProjectFolderKind::Empty {
            return Err(BridgeError::path(
                "existing projects require native source-video confirmation",
            ));
        }
        self.lock()?.roots.insert(inspection.path.clone());
        Ok(inspection.path)
    }

    pub fn inspect_project_folder(&self, path: &Path) -> BridgeResult<ProjectFolderInspection> {
        inspect_project_folder(path)
    }

    pub fn approve_existing_project(
        &self,
        path: &Path,
        selected_source: &Path,
    ) -> BridgeResult<PathBuf> {
        let inspection = inspect_project_folder(path)?;
        if inspection.kind != ProjectFolderKind::Existing {
            return Err(BridgeError::path(
                "the selected folder is not an existing Parallax Forge project",
            ));
        }

        // The stored source is untrusted project metadata. Validate it lexically,
        // but never canonicalize, stat, or open it. Only the newly picker-granted
        // selection is probed and canonicalized before the paths are compared.
        let marker = read_project_marker(&inspection.path)?;
        let selected_source = canonical_existing_file(selected_source)?;
        ensure_extension(&selected_source, VIDEO_EXTENSIONS, "video")?;
        if !stored_source_matches_selection(&marker.input_path, &selected_source)? {
            return Err(BridgeError::path(
                "selected video does not match the source recorded by this project",
            ));
        }

        // Commit both grants together. A failed source comparison must not leave
        // either a project-wide or file-specific permission behind.
        let mut approved = self.lock()?;
        approved.roots.insert(inspection.path.clone());
        approved.files.insert(selected_source.clone());
        approved
            .project_sources
            .insert(inspection.path.clone(), selected_source);
        Ok(inspection.path)
    }

    pub fn approve_output(&self, path: &Path, extension: &str) -> BridgeResult<PathBuf> {
        let path = resolve_output_path(path)?;
        ensure_extension(&path, &[extension], "output")?;
        self.lock()?.output_files.insert(path.clone());
        Ok(path)
    }

    pub fn resolve_allowed_video(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = canonical_existing_file(path)?;
        ensure_extension(&path, VIDEO_EXTENSIONS, "video")?;
        if self.is_file_allowed(&path)? {
            Ok(path)
        } else {
            Err(BridgeError::path(
                "video path was not granted by the native file picker",
            ))
        }
    }

    pub fn resolve_allowed_directory(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = canonical_existing_directory(path)?;
        if self.is_under_approved_root(&path)? {
            Ok(path)
        } else {
            Err(BridgeError::path(
                "directory was not granted by the native folder picker",
            ))
        }
    }

    pub fn resolve_allowed_directory_candidate(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = resolve_candidate_path(path)?;
        if self.is_under_approved_root(&path)? {
            Ok(path)
        } else {
            Err(BridgeError::path(
                "directory must be inside a folder granted by the native picker",
            ))
        }
    }

    pub fn resolve_allowed_existing_file(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = canonical_existing_file(path)?;
        if self.is_file_allowed(&path)? || self.is_under_approved_root(&path)? {
            Ok(path)
        } else {
            Err(BridgeError::path(
                "file was not granted by a native picker and is outside the active project",
            ))
        }
    }

    pub fn resolve_allowed_config_file(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = self.resolve_allowed_existing_file(path)?;
        ensure_extension(&path, CONFIG_EXTENSIONS, "configuration")?;
        Ok(path)
    }

    pub fn resolve_allowed_preview_file(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = canonical_existing_file(path)?;
        ensure_extension(&path, PREVIEW_EXTENSIONS, "preview")?;
        if self.is_file_allowed(&path)? || self.is_under_approved_root(&path)? {
            Ok(path)
        } else {
            Err(BridgeError::path(
                "preview is outside the selected source, output, and active project",
            ))
        }
    }

    pub fn resolve_allowed_output(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = resolve_output_path(path)?;
        let approved = self.lock()?;
        if approved.output_files.contains(&path)
            || approved.roots.iter().any(|root| path.starts_with(root))
        {
            Ok(path)
        } else {
            Err(BridgeError::path(
                "output path was not granted by the save dialog or active project folder",
            ))
        }
    }

    pub fn resolve_allowed_existing_any(&self, path: &Path) -> BridgeResult<PathBuf> {
        let path = std::fs::canonicalize(path)
            .map_err(|_| BridgeError::path("the requested path does not exist"))?;
        let approved = self.lock()?;
        if approved.files.contains(&path)
            || approved.output_files.contains(&path)
            || approved.roots.iter().any(|root| path.starts_with(root))
        {
            Ok(path)
        } else {
            Err(BridgeError::path(
                "the requested path is outside granted locations",
            ))
        }
    }

    fn is_file_allowed(&self, path: &Path) -> BridgeResult<bool> {
        let approved = self.lock()?;
        Ok(approved.files.contains(path) || approved.output_files.contains(path))
    }

    fn is_under_approved_root(&self, path: &Path) -> BridgeResult<bool> {
        Ok(self
            .lock()?
            .roots
            .iter()
            .any(|root| path == root || path.starts_with(root)))
    }

    fn resolve_project_base(
        &self,
        base_directory: Option<&Path>,
        create_default: bool,
    ) -> BridgeResult<PathBuf> {
        if let Some(base) = base_directory {
            let base = canonical_existing_directory(base)?;
            if self.lock()?.project_bases.contains(&base) {
                return Ok(base);
            }
            return Err(BridgeError::path(
                "custom project base was not granted by the native folder picker",
            ));
        }
        resolve_default_project_base(create_default)
    }

    fn bind_new_project_source(&self, root: &Path, source: &Path) -> BridgeResult<()> {
        let mut approved = self.lock()?;
        if !approved.roots.contains(root) || !approved.files.contains(source) {
            return Err(BridgeError::path(
                "new project paths were not granted by native pickers",
            ));
        }
        if let Some(existing) = approved.project_sources.get(root) {
            if existing == source {
                return Ok(());
            }
            return Err(BridgeError::path(
                "project source confirmation does not match the selected video",
            ));
        }
        approved
            .project_sources
            .insert(root.to_owned(), source.to_owned());
        Ok(())
    }

    fn validate_project_source_binding(&self, root: &Path) -> BridgeResult<()> {
        let confirmed_source =
            self.lock()?
                .project_sources
                .get(root)
                .cloned()
                .ok_or_else(|| {
                    BridgeError::path("project source has not been confirmed by a native picker")
                })?;
        let marker = read_project_marker(root)?;
        if !stored_source_matches_selection(&marker.input_path, &confirmed_source)? {
            return Err(BridgeError::path(
                "project source reference changed after native confirmation",
            ));
        }
        validate_project_artifacts(root)
    }

    fn lock(&self) -> BridgeResult<MutexGuard<'_, ApprovedPaths>> {
        self.inner
            .lock()
            .map_err(|_| BridgeError::internal("path policy lock was poisoned"))
    }
}

fn resolve_default_project_base(create: bool) -> BridgeResult<PathBuf> {
    resolve_managed_project_base_at(Path::new(DEFAULT_PROJECT_BASE), create).map_err(|_| {
        BridgeError::path(format!(
            "default project location is unavailable: {DEFAULT_PROJECT_BASE}; choose another project location"
        ))
    })
}

fn resolve_managed_project_base_at(base: &Path, create: bool) -> BridgeResult<PathBuf> {
    match fs::symlink_metadata(base) {
        Ok(_) => return canonical_normal_directory(base, "project location"),
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(_) => return Err(BridgeError::path("project location could not be inspected")),
    }

    let parent = base
        .parent()
        .ok_or_else(|| BridgeError::path("project location has no parent folder"))?;
    let parent = canonical_normal_directory(parent, "project location parent")?;
    let leaf = base
        .file_name()
        .ok_or_else(|| BridgeError::path("project location has no folder name"))?;
    let candidate = parent.join(leaf);
    if !create {
        return Ok(candidate);
    }

    match fs::create_dir(&candidate) {
        Ok(()) => {}
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {}
        Err(_) => return Err(BridgeError::path("project location could not be created")),
    }
    canonical_normal_directory(&candidate, "project location")
}

fn canonical_normal_directory(path: &Path, label: &str) -> BridgeResult<PathBuf> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| BridgeError::path(format!("{label} does not exist or is not accessible")))?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() || is_reparse_point(&metadata) {
        return Err(BridgeError::path(format!(
            "{label} must be a normal local folder"
        )));
    }
    fs::canonicalize(path).map_err(|_| BridgeError::path(format!("{label} could not be resolved")))
}

#[cfg(test)]
fn plan_new_project_at_base(source: &Path, base: &Path) -> BridgeResult<NewProjectPlan> {
    plan_new_project_at_bases(source, base, base)
}

fn plan_new_project_at_bases(
    source: &Path,
    inspected_base: &Path,
    display_base: &Path,
) -> BridgeResult<NewProjectPlan> {
    let project_name = safe_project_name(source)?;
    let (_, folder_name, collision_index) =
        next_available_project_directory(inspected_base, &project_name)?;
    new_project_plan(source, display_base, folder_name, collision_index, false)
}

fn new_project_plan(
    source: &Path,
    base: &Path,
    folder_name: String,
    collision_index: usize,
    created: bool,
) -> BridgeResult<NewProjectPlan> {
    let source_name = source
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| BridgeError::path("source video has no valid Unicode file name"))?;
    Ok(NewProjectPlan {
        source_path: path_to_string(source)?,
        source_name: source_name.to_owned(),
        base_directory: path_to_string(base)?,
        project_directory: path_to_string(&base.join(&folder_name))?,
        project_name: folder_name.clone(),
        folder_name,
        collision_index,
        created,
    })
}

fn safe_project_name(source: &Path) -> BridgeResult<String> {
    let stem = source
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or_else(|| BridgeError::path("source video has no valid Unicode name"))?;
    Ok(sanitize_project_name(stem))
}

fn sanitize_project_name(raw: &str) -> String {
    let mut sanitized = String::new();
    let mut separator_pending = false;
    for character in raw.chars() {
        let unsafe_character = character.is_control()
            || character.is_whitespace()
            || character == '_'
            || matches!(
                character,
                '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*'
            );
        if unsafe_character {
            separator_pending = !sanitized.is_empty();
            continue;
        }
        if separator_pending {
            sanitized.push(' ');
            separator_pending = false;
        }
        sanitized.push(character);
    }

    let mut sanitized = truncate_utf16(
        sanitized.trim_matches(|character| character == ' ' || character == '.'),
        MAX_PROJECT_NAME_UTF16,
    );
    while sanitized.ends_with([' ', '.']) {
        sanitized.pop();
    }
    if sanitized.is_empty() {
        sanitized = "Untitled Project".to_owned();
    }
    if is_windows_reserved_name(&sanitized) {
        const DEVICE_SUFFIX: &str = " Project";
        let maximum_stem = MAX_PROJECT_NAME_UTF16 - DEVICE_SUFFIX.encode_utf16().count();
        // Windows treats device names as reserved even when they have an
        // extension (for example `CON.video`). Remove those separator dots so
        // the visible suffix changes the device stem itself.
        sanitized = sanitized.replace('.', " ");
        sanitized = truncate_utf16(&sanitized, maximum_stem);
        while sanitized.ends_with([' ', '.']) {
            sanitized.pop();
        }
        sanitized.push_str(DEVICE_SUFFIX);
    }
    sanitized
}

fn truncate_utf16(value: &str, maximum_units: usize) -> String {
    let mut used = 0;
    value
        .chars()
        .take_while(|character| {
            let units = character.len_utf16();
            if used + units > maximum_units {
                return false;
            }
            used += units;
            true
        })
        .collect()
}

fn is_windows_reserved_name(value: &str) -> bool {
    let device = value
        .trim_end_matches([' ', '.'])
        .split('.')
        .next()
        .unwrap_or_default()
        .to_ascii_uppercase();
    if matches!(device.as_str(), "CON" | "PRN" | "AUX" | "NUL") {
        return true;
    }
    let mut characters = device.chars();
    let prefix: String = characters.by_ref().take(3).collect();
    let digit = characters.next();
    characters.next().is_none()
        && matches!(prefix.as_str(), "COM" | "LPT")
        && digit
            .is_some_and(|value| matches!(value, '1'..='9' | '\u{00b9}' | '\u{00b2}' | '\u{00b3}'))
}

fn folder_name_for_index(project_name: &str, collision_index: usize) -> String {
    let suffix = if collision_index == 0 {
        String::new()
    } else {
        format!(" ({collision_index})")
    };
    let maximum_prefix = MAX_PROJECT_NAME_UTF16.saturating_sub(suffix.encode_utf16().count());
    let mut prefix = truncate_utf16(project_name, maximum_prefix);
    while prefix.ends_with([' ', '.']) {
        prefix.pop();
    }
    if prefix.is_empty() {
        prefix = truncate_utf16("Untitled Project", maximum_prefix);
    }
    format!("{prefix}{suffix}")
}

fn next_available_project_directory(
    base: &Path,
    project_name: &str,
) -> BridgeResult<(PathBuf, String, usize)> {
    for attempt in 0..MAX_PROJECT_COLLISION_INDEX {
        let collision_index = if attempt == 0 { 0 } else { attempt + 1 };
        let folder_name = folder_name_for_index(project_name, collision_index);
        let candidate = base.join(&folder_name);
        match fs::symlink_metadata(&candidate) {
            Ok(_) => continue,
            Err(error) if error.kind() == ErrorKind::NotFound => {
                return Ok((candidate, folder_name, collision_index));
            }
            Err(_) => {
                return Err(BridgeError::path(
                    "project destination could not be inspected",
                ))
            }
        }
    }
    Err(BridgeError::path(
        "no collision-free project folder name is available",
    ))
}

fn create_unique_project_directory(
    base: &Path,
    project_name: &str,
) -> BridgeResult<(PathBuf, String, usize)> {
    for attempt in 0..MAX_PROJECT_COLLISION_INDEX {
        let collision_index = if attempt == 0 { 0 } else { attempt + 1 };
        let folder_name = folder_name_for_index(project_name, collision_index);
        let candidate = base.join(&folder_name);
        match fs::create_dir(&candidate) {
            Ok(()) => {
                let project = validate_created_project_directory(base, &candidate)?;
                return Ok((project, folder_name, collision_index));
            }
            Err(error) if error.kind() == ErrorKind::AlreadyExists => continue,
            Err(_) => {
                return Err(BridgeError::path(
                    "project folder could not be created in the selected location",
                ))
            }
        }
    }
    Err(BridgeError::path(
        "no collision-free project folder name is available",
    ))
}

fn validate_created_project_directory(base: &Path, candidate: &Path) -> BridgeResult<PathBuf> {
    let metadata = fs::symlink_metadata(candidate)
        .map_err(|_| BridgeError::path("created project folder could not be inspected"))?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() || is_reparse_point(&metadata) {
        return Err(BridgeError::path(
            "created project folder has an unsafe file type",
        ));
    }
    let project = fs::canonicalize(candidate)
        .map_err(|_| BridgeError::path("created project folder could not be resolved"))?;
    if project.parent() != Some(base) {
        return Err(BridgeError::path(
            "created project folder resolves outside the selected location",
        ));
    }
    let mut entries = fs::read_dir(&project)
        .map_err(|_| BridgeError::path("created project folder could not be inspected"))?;
    if entries.next().is_some() {
        return Err(BridgeError::path(
            "created project folder was modified before it could be secured",
        ));
    }
    Ok(project)
}

#[cfg(windows)]
fn is_reparse_point(metadata: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

#[cfg(not(windows))]
fn is_reparse_point(_metadata: &fs::Metadata) -> bool {
    false
}

pub fn normalize_extension(extension: &str) -> BridgeResult<String> {
    let extension = extension
        .trim()
        .trim_start_matches('.')
        .to_ascii_lowercase();
    if !OUTPUT_EXTENSIONS.contains(&extension.as_str()) {
        return Err(BridgeError::invalid(format!(
            "unsupported output extension: {extension}"
        )));
    }
    Ok(extension)
}

pub fn safe_suggested_filename(name: &str, extension: &str) -> BridgeResult<String> {
    let name = name.trim();
    if name.is_empty()
        || name.len() > 180
        || name == "."
        || name == ".."
        || name.chars().any(char::is_control)
        || name.contains(['/', '\\', ':', '*', '?', '"', '<', '>', '|'])
    {
        return Err(BridgeError::invalid(
            "suggested output name is not a safe file name",
        ));
    }

    let mut path = PathBuf::from(name);
    match path.extension().and_then(|value| value.to_str()) {
        Some(current) if current.eq_ignore_ascii_case(extension) => {}
        Some(_) => {
            return Err(BridgeError::invalid(
                "suggested output name extension does not match the requested format",
            ))
        }
        None => {
            path.set_extension(extension);
        }
    }

    path.into_os_string()
        .into_string()
        .map_err(|_| BridgeError::invalid("suggested output name is not valid Unicode"))
}

pub fn path_to_string(path: &Path) -> BridgeResult<String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| BridgeError::path("path cannot be represented safely as UTF-8"))
}

pub fn validate_worker_paths(
    method: WorkerMethod,
    params: &mut Map<String, Value>,
    policy: &PathPolicy,
) -> BridgeResult<()> {
    for (key, value) in params.iter_mut() {
        validate_path_value(method, key, value, policy)?;
    }
    validate_worker_project_binding(method, params, policy)
}

fn validate_worker_project_binding(
    method: WorkerMethod,
    params: &Map<String, Value>,
    policy: &PathPolicy,
) -> BridgeResult<()> {
    let Some(project_dir) = params.get("project_dir").and_then(Value::as_str) else {
        return Ok(());
    };
    let project_dir = Path::new(project_dir);
    if method == WorkerMethod::CreateProject {
        let source = params
            .get("input_path")
            .and_then(Value::as_str)
            .ok_or_else(|| BridgeError::invalid("create project has no validated source path"))?;
        policy.bind_new_project_source(project_dir, Path::new(source))
    } else {
        policy.validate_project_source_binding(project_dir)
    }
}

fn validate_path_value(
    method: WorkerMethod,
    key: &str,
    value: &mut Value,
    policy: &PathPolicy,
) -> BridgeResult<()> {
    let category = path_category(key);
    match (category, value) {
        (Some(PathCategory::Video), Value::String(raw)) => {
            *raw = path_to_string(&policy.resolve_allowed_video(Path::new(raw))?)?;
        }
        (Some(PathCategory::Directory), Value::String(raw)) => {
            let resolved = if method == WorkerMethod::CreateProject {
                policy.resolve_allowed_directory_candidate(Path::new(raw))?
            } else {
                policy.resolve_allowed_directory(Path::new(raw))?
            };
            *raw = path_to_string(&resolved)?;
        }
        (Some(PathCategory::DirectoryCandidate), Value::String(raw)) => {
            *raw = path_to_string(&policy.resolve_allowed_directory_candidate(Path::new(raw))?)?;
        }
        (Some(PathCategory::Config), Value::String(raw)) => {
            *raw = path_to_string(&policy.resolve_allowed_config_file(Path::new(raw))?)?;
        }
        (Some(PathCategory::ExistingFile), Value::String(raw)) => {
            *raw = path_to_string(&policy.resolve_allowed_existing_file(Path::new(raw))?)?;
        }
        (Some(PathCategory::Output), Value::String(raw)) => {
            *raw = path_to_string(&policy.resolve_allowed_output(Path::new(raw))?)?;
        }
        (Some(_), _) => {
            return Err(BridgeError::invalid(format!(
                "path parameter '{key}' must be a string"
            )))
        }
        (None, Value::Object(object)) => {
            for (nested_key, nested_value) in object.iter_mut() {
                validate_path_value(method, nested_key, nested_value, policy)?;
            }
        }
        (None, _) if looks_like_path_key(key) => {
            return Err(BridgeError::invalid(format!(
                "unrecognized path parameter '{key}'"
            )))
        }
        _ => {}
    }
    Ok(())
}

#[derive(Debug, Clone, Copy)]
enum PathCategory {
    Video,
    Directory,
    DirectoryCandidate,
    Config,
    ExistingFile,
    Output,
}

fn path_category(key: &str) -> Option<PathCategory> {
    match key {
        "input" | "input_path" | "input_video" | "source" | "source_path" | "source_video"
        | "video" | "video_path" => Some(PathCategory::Video),
        "directory" | "project_directory" | "project_dir" | "project_path" | "work_dir" => {
            Some(PathCategory::Directory)
        }
        "cache_dir" => Some(PathCategory::DirectoryCandidate),
        "config_path" => Some(PathCategory::Config),
        "project_file" | "script_path" | "stereo_script_path" => Some(PathCategory::ExistingFile),
        "destination" | "destination_path" | "output" | "output_path" => Some(PathCategory::Output),
        _ => None,
    }
}

fn looks_like_path_key(key: &str) -> bool {
    key.ends_with("_path")
        || key.ends_with("_dir")
        || key.ends_with("_directory")
        || matches!(
            key,
            "input" | "output" | "source" | "destination" | "directory"
        )
}

fn canonical_existing_file(path: &Path) -> BridgeResult<PathBuf> {
    let path = std::fs::canonicalize(path)
        .map_err(|_| BridgeError::path("selected file does not exist or is not accessible"))?;
    if !path.is_file() {
        return Err(BridgeError::path("selected path is not a file"));
    }
    Ok(path)
}

fn canonical_existing_directory(path: &Path) -> BridgeResult<PathBuf> {
    let path = std::fs::canonicalize(path)
        .map_err(|_| BridgeError::path("selected folder does not exist or is not accessible"))?;
    if !path.is_dir() {
        return Err(BridgeError::path("selected path is not a folder"));
    }
    Ok(path)
}

fn inspect_project_folder(path: &Path) -> BridgeResult<ProjectFolderInspection> {
    let path = canonical_existing_directory(path)?;
    let mut entries = fs::read_dir(&path)
        .map_err(|_| BridgeError::path("selected folder could not be inspected"))?;
    match entries.next() {
        None => Ok(ProjectFolderInspection {
            path,
            kind: ProjectFolderKind::Empty,
        }),
        Some(Err(_)) => Err(BridgeError::path(
            "selected folder entries could not be inspected",
        )),
        Some(Ok(_)) => {
            read_project_marker(&path)?;
            validate_project_artifacts(&path)?;
            Ok(ProjectFolderInspection {
                path,
                kind: ProjectFolderKind::Existing,
            })
        }
    }
}

fn read_project_marker(root: &Path) -> BridgeResult<ProjectMarker> {
    let bytes = read_bounded_project_file(
        root,
        Path::new("project.json"),
        MAX_PROJECT_MARKER_BYTES,
        "project.json",
    )?
    .ok_or_else(|| {
        BridgeError::path("choose an empty folder or an existing Parallax Forge project")
    })?;
    let project: Value = serde_json::from_slice(&bytes)
        .map_err(|_| BridgeError::path("project.json is not valid JSON"))?;
    let object = project
        .as_object()
        .ok_or_else(|| BridgeError::path("project.json must contain a project object"))?;
    let schema_ok = object.get("schema_version").and_then(Value::as_str) == Some("1.0");
    let name_ok = object
        .get("name")
        .and_then(Value::as_str)
        .is_some_and(|value| {
            !value.trim().is_empty()
                && value.chars().count() <= 160
                && !value.chars().any(char::is_control)
        });
    let input_path = object
        .get("input_path")
        .and_then(Value::as_str)
        .ok_or_else(|| BridgeError::path("project.json has no valid source-video reference"))?;
    if !schema_ok || !name_ok || validate_stored_source_path(input_path).is_err() {
        return Err(BridgeError::path(
            "project.json is not a recognized Parallax Forge project",
        ));
    }
    Ok(ProjectMarker {
        input_path: input_path.to_owned(),
    })
}

fn validate_stored_source_path(raw: &str) -> BridgeResult<()> {
    if raw.is_empty()
        || raw.len() > 32 * 1024
        || raw.chars().any(char::is_control)
        || raw.contains('\0')
    {
        return Err(BridgeError::path(
            "project source reference is not a safe path",
        ));
    }
    let path = Path::new(raw);
    if !path.is_absolute()
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(BridgeError::path(
            "project source reference must be an absolute normalized path",
        ));
    }
    ensure_extension(path, VIDEO_EXTENSIONS, "video")
}

fn stored_source_matches_selection(raw: &str, selected: &Path) -> BridgeResult<bool> {
    validate_stored_source_path(raw)?;
    Ok(Path::new(raw) == selected)
}

#[derive(Debug, Clone, Copy)]
enum ProjectEntryKind {
    File,
    Directory,
}

fn validate_project_artifacts(root: &Path) -> BridgeResult<()> {
    for directory in PROJECT_DIRECTORIES {
        validate_optional_project_entry(
            root,
            Path::new(directory),
            ProjectEntryKind::Directory,
            "project artifact directory",
        )?;
    }
    for file in FIXED_PROJECT_FILES {
        validate_optional_project_entry(
            root,
            Path::new(file),
            ProjectEntryKind::File,
            "project artifact",
        )?;
    }
    validate_depth_manifest_paths(root)
}

fn validate_depth_manifest_paths(root: &Path) -> BridgeResult<()> {
    let Some(bytes) = read_bounded_project_file(
        root,
        Path::new("depth/metadata.json"),
        MAX_DEPTH_MANIFEST_BYTES,
        "depth metadata",
    )?
    else {
        return Ok(());
    };
    let manifest: Value = serde_json::from_slice(&bytes)
        .map_err(|_| BridgeError::path("depth metadata is not valid JSON"))?;
    let object = manifest
        .as_object()
        .ok_or_else(|| BridgeError::path("depth metadata must contain an object"))?;
    if object.get("schema_version").and_then(Value::as_str) != Some("1.0") {
        return Err(BridgeError::path(
            "depth metadata has an unsupported schema version",
        ));
    }
    let shots = object
        .get("shots")
        .and_then(Value::as_array)
        .ok_or_else(|| BridgeError::path("depth metadata has no valid shot list"))?;
    for shot in shots {
        let path = shot
            .as_object()
            .and_then(|item| item.get("path"))
            .and_then(Value::as_str)
            .ok_or_else(|| BridgeError::path("depth metadata has an invalid artifact path"))?;
        validate_depth_artifact_reference(root, path)?;
    }
    Ok(())
}

fn validate_depth_artifact_reference(root: &Path, raw: &str) -> BridgeResult<()> {
    if raw.is_empty() || raw.len() > 32 * 1024 || raw.chars().any(char::is_control) {
        return Err(BridgeError::path(
            "depth metadata has an invalid artifact path",
        ));
    }
    let relative = Path::new(raw);
    ensure_safe_project_relative_path(relative)?;
    if relative.parent() != Some(Path::new("depth")) {
        return Err(BridgeError::path(
            "depth artifacts must remain directly inside the project depth folder",
        ));
    }
    ensure_extension(relative, &["npz"], "depth artifact")?;
    validate_optional_project_entry(root, relative, ProjectEntryKind::File, "depth artifact")?;
    Ok(())
}

fn ensure_safe_project_relative_path(path: &Path) -> BridgeResult<()> {
    if path.is_absolute() {
        return Err(BridgeError::path("project artifact path must be relative"));
    }
    let mut saw_component = false;
    for component in path.components() {
        let Component::Normal(value) = component else {
            return Err(BridgeError::path(
                "project artifact path contains traversal components",
            ));
        };
        saw_component = true;
        let value = value
            .to_str()
            .ok_or_else(|| BridgeError::path("project artifact path is not valid Unicode"))?;
        if value.is_empty()
            || value.chars().any(char::is_control)
            || value.contains([':', '*', '?', '"', '<', '>', '|'])
            || value.contains(['/', '\\'])
        {
            return Err(BridgeError::path(
                "project artifact path contains an unsafe component",
            ));
        }
    }
    if !saw_component {
        return Err(BridgeError::path("project artifact path is empty"));
    }
    Ok(())
}

fn validate_optional_project_entry(
    root: &Path,
    relative: &Path,
    kind: ProjectEntryKind,
    label: &str,
) -> BridgeResult<Option<fs::Metadata>> {
    ensure_safe_project_relative_path(relative)?;
    let candidate = root.join(relative);
    let metadata = match fs::symlink_metadata(&candidate) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(BridgeError::path(format!("{label} could not be inspected"))),
    };
    if metadata.file_type().is_symlink() {
        return Err(BridgeError::path(format!(
            "{label} may not be a symbolic link"
        )));
    }
    let kind_matches = match kind {
        ProjectEntryKind::File => metadata.is_file(),
        ProjectEntryKind::Directory => metadata.is_dir(),
    };
    if !kind_matches {
        return Err(BridgeError::path(format!(
            "{label} has an unexpected file type"
        )));
    }
    let canonical = fs::canonicalize(&candidate)
        .map_err(|_| BridgeError::path(format!("{label} could not be resolved")))?;
    if !canonical.starts_with(root) {
        return Err(BridgeError::path(format!(
            "{label} resolves outside the selected project"
        )));
    }
    Ok(Some(metadata))
}

fn read_bounded_project_file(
    root: &Path,
    relative: &Path,
    maximum_bytes: u64,
    label: &str,
) -> BridgeResult<Option<Vec<u8>>> {
    let Some(metadata) =
        validate_optional_project_entry(root, relative, ProjectEntryKind::File, label)?
    else {
        return Ok(None);
    };
    if metadata.len() == 0 || metadata.len() > maximum_bytes {
        return Err(BridgeError::path(format!(
            "{label} is empty or unexpectedly large"
        )));
    }
    let candidate = root.join(relative);
    let bytes = fs::read(&candidate)
        .map_err(|_| BridgeError::path(format!("{label} could not be read")))?;
    if bytes.is_empty() || bytes.len() as u64 > maximum_bytes {
        return Err(BridgeError::path(format!(
            "{label} changed size while it was being inspected"
        )));
    }
    validate_optional_project_entry(root, relative, ProjectEntryKind::File, label)?;
    Ok(Some(bytes))
}

fn resolve_candidate_path(path: &Path) -> BridgeResult<PathBuf> {
    if path.exists() {
        return canonical_existing_directory(path);
    }
    ensure_safe_leaf(path)?;
    let parent = path
        .parent()
        .ok_or_else(|| BridgeError::path("project path has no parent folder"))?;
    let parent = canonical_existing_directory(parent)?;
    let leaf = path
        .file_name()
        .ok_or_else(|| BridgeError::path("project path has no folder name"))?;
    Ok(parent.join(leaf))
}

fn resolve_output_path(path: &Path) -> BridgeResult<PathBuf> {
    if path.exists() {
        return canonical_existing_file(path);
    }
    ensure_safe_leaf(path)?;
    let parent = path
        .parent()
        .ok_or_else(|| BridgeError::path("output path has no parent folder"))?;
    let parent = canonical_existing_directory(parent)?;
    let leaf = path
        .file_name()
        .ok_or_else(|| BridgeError::path("output path has no file name"))?;
    Ok(parent.join(leaf))
}

fn ensure_safe_leaf(path: &Path) -> BridgeResult<()> {
    if path
        .components()
        .any(|component| matches!(component, Component::ParentDir))
    {
        return Err(BridgeError::path(
            "path traversal components are not allowed",
        ));
    }
    let leaf = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| BridgeError::path("path must end in a valid Unicode name"))?;
    if leaf.is_empty() || leaf == "." || leaf == ".." || leaf.contains('\0') {
        return Err(BridgeError::path("path has an invalid final component"));
    }
    Ok(())
}

fn ensure_extension(path: &Path, allowed: &[&str], label: &str) -> BridgeResult<()> {
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .ok_or_else(|| BridgeError::path(format!("{label} path has no file extension")))?;
    if !allowed.contains(&extension.as_str()) {
        return Err(BridgeError::path(format!(
            "unsupported {label} extension: {extension}"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io;
    use tempfile::tempdir;

    fn write_project_marker(root: &Path, source: &Path) {
        let source = canonical_existing_file(source).expect("canonical source fixture");
        write_project_marker_raw(
            root,
            source.to_str().expect("Unicode temporary source path"),
        );
    }

    fn write_project_marker_raw(root: &Path, source: &str) {
        let marker = serde_json::json!({
            "schema_version": "1.0",
            "name": "Demo",
            "input_path": source,
        });
        fs::write(
            root.join("project.json"),
            serde_json::to_vec(&marker).expect("serialize project marker"),
        )
        .expect("write project marker");
    }

    fn create_file_symlink(target: &Path, link: &Path) -> io::Result<()> {
        #[cfg(windows)]
        {
            std::os::windows::fs::symlink_file(target, link)
        }
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(target, link)
        }
        #[cfg(not(any(windows, unix)))]
        {
            let _ = (target, link);
            Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "symlinks are unavailable on this platform",
            ))
        }
    }

    fn create_directory_symlink(target: &Path, link: &Path) -> io::Result<()> {
        #[cfg(windows)]
        {
            std::os::windows::fs::symlink_dir(target, link)
        }
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(target, link)
        }
        #[cfg(not(any(windows, unix)))]
        {
            let _ = (target, link);
            Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "symlinks are unavailable on this platform",
            ))
        }
    }

    #[test]
    fn safe_name_adds_requested_extension() {
        assert_eq!(
            safe_suggested_filename("stereo render", "mp4").expect("safe name"),
            "stereo render.mp4"
        );
    }

    #[test]
    fn safe_name_rejects_path_components() {
        assert!(safe_suggested_filename("../render", "mp4").is_err());
        assert!(safe_suggested_filename("folder/render", "mp4").is_err());
    }

    #[test]
    fn default_project_location_is_the_product_owned_windows_path() {
        assert_eq!(DEFAULT_PROJECT_BASE, r"D:\Parallax Projects");
    }

    #[test]
    fn project_names_are_windows_safe_and_device_names_are_disarmed() {
        assert_eq!(
            sanitize_project_name("  Film<>:\"/\\|?*\0Name...  "),
            "Film Name"
        );
        assert_eq!(sanitize_project_name("."), "Untitled Project");
        assert_eq!(sanitize_project_name("CON"), "CON Project");
        assert_eq!(sanitize_project_name("com1.backup"), "com1 backup Project");
        assert_eq!(sanitize_project_name("LPT9. "), "LPT9 Project");
        assert_eq!(sanitize_project_name("COM\u{00b9}"), "COM\u{00b9} Project");
        assert_eq!(
            sanitize_project_name("lpt\u{00b3}.proxy"),
            "lpt\u{00b3} proxy Project"
        );
        assert_eq!(sanitize_project_name("COM0"), "COM0");
        assert_eq!(sanitize_project_name("my_cool__film"), "my cool film");

        let escaped = sanitize_project_name(r"..\..\AUX:*? trailer");
        assert!(!escaped.contains(['/', '\\', ':', '*', '?']));
        assert!(!escaped.ends_with([' ', '.']));
        assert!(!is_windows_reserved_name(&escaped));
    }

    #[test]
    fn long_reserved_names_keep_their_disarming_suffix() {
        for alias in [
            "COM\u{00b9}",
            "COM\u{00b2}",
            "COM\u{00b3}",
            "LPT\u{00b9}",
            "LPT\u{00b2}",
            "LPT\u{00b3}",
        ] {
            assert!(is_windows_reserved_name(alias));
        }
        for reserved in [
            format!("CON.{}", "x".repeat(200)),
            format!("LPT1.{}", "🎬".repeat(100)),
            format!("COM\u{00b2}.{}", "x".repeat(200)),
            format!("LPT\u{00b3}.{}", "🎬".repeat(100)),
        ] {
            let sanitized = sanitize_project_name(&reserved);
            assert!(sanitized.ends_with(" Project"));
            assert!(sanitized.encode_utf16().count() <= MAX_PROJECT_NAME_UTF16);
            assert!(!is_windows_reserved_name(&sanitized));
        }
    }

    #[test]
    fn project_names_and_collision_suffixes_are_utf16_bounded() {
        let name = sanitize_project_name(&"🎬".repeat(100));
        assert!(name.encode_utf16().count() <= MAX_PROJECT_NAME_UTF16);
        assert!(name.is_char_boundary(name.len()));

        let collided = folder_name_for_index(&name, 10_000);
        assert!(collided.encode_utf16().count() <= MAX_PROJECT_NAME_UTF16);
        assert!(collided.ends_with(" (10000)"));
    }

    #[test]
    fn planning_is_read_only_even_when_the_base_does_not_exist() {
        let directory = tempdir().expect("temporary directory");
        let source = directory.path().join("A Night in Montréal.mp4");
        let missing_base = directory.path().join("projects-not-created-yet");
        fs::write(&source, b"fixture").expect("write source fixture");

        let plan = plan_new_project_at_base(&source, &missing_base).expect("read-only plan");

        assert!(!missing_base.exists());
        assert!(!Path::new(&plan.project_directory).exists());
        assert_eq!(plan.source_name, "A Night in Montréal.mp4");
        assert_eq!(plan.project_name, "A Night in Montréal");
        assert_eq!(plan.folder_name, "A Night in Montréal");
        assert_eq!(plan.collision_index, 0);
        assert!(!plan.created);
        let encoded = serde_json::to_value(&plan).expect("serialize plan");
        assert!(encoded.get("sourcePath").is_some());
        assert!(encoded.get("projectDirectory").is_some());
        assert!(encoded.get("collisionIndex").is_some());
        assert!(encoded.get("source_path").is_none());
    }

    #[test]
    fn default_plan_fields_preserve_the_exact_product_path() {
        let directory = tempdir().expect("temporary directory");
        let source = directory.path().join("Default_Path.mp4");
        let inspected_base = directory.path().join("inspected-base");
        fs::write(&source, b"fixture").expect("write source fixture");
        fs::create_dir(&inspected_base).expect("create inspected base");

        let plan =
            plan_new_project_at_bases(&source, &inspected_base, Path::new(DEFAULT_PROJECT_BASE))
                .expect("plan with exact display base");

        assert_eq!(plan.base_directory, DEFAULT_PROJECT_BASE);
        assert_eq!(
            plan.project_directory,
            path_to_string(&Path::new(DEFAULT_PROJECT_BASE).join("Default Path"))
                .expect("Unicode default project path")
        );
        assert_eq!(plan.project_name, "Default Path");
        assert_eq!(plan.collision_index, 0);
    }

    #[test]
    fn managed_base_planning_validates_the_parent_without_creating() {
        let directory = tempdir().expect("temporary directory");
        let base = directory.path().join("Parallax Projects");

        let planned =
            resolve_managed_project_base_at(&base, false).expect("validate base candidate");
        assert_eq!(
            planned.parent(),
            Some(
                fs::canonicalize(directory.path())
                    .expect("canonical parent")
                    .as_path()
            )
        );
        assert!(!base.exists());

        let created = resolve_managed_project_base_at(&base, true).expect("create validated base");
        assert!(created.is_dir());
        assert_eq!(created, fs::canonicalize(&base).expect("canonical base"));
    }

    #[test]
    fn managed_base_rejects_missing_parents_files_and_links() {
        let directory = tempdir().expect("temporary directory");
        let missing_parent = directory.path().join("missing").join("projects");
        assert!(resolve_managed_project_base_at(&missing_parent, false).is_err());

        let file = directory.path().join("file-base");
        fs::write(&file, b"fixture").expect("write file fixture");
        assert!(resolve_managed_project_base_at(&file, false).is_err());

        let target = directory.path().join("target-base");
        let link = directory.path().join("linked-base");
        fs::create_dir(&target).expect("create link target");
        if let Err(error) = create_directory_symlink(&target, &link) {
            if matches!(
                error.kind(),
                io::ErrorKind::PermissionDenied | io::ErrorKind::Unsupported
            ) {
                return;
            }
            panic!("could not create directory symlink fixture: {error}");
        }
        assert!(resolve_managed_project_base_at(&link, false).is_err());
    }

    #[test]
    fn custom_bases_and_sources_require_separate_native_picker_grants() {
        let directory = tempdir().expect("temporary directory");
        let base = directory.path().join("projects");
        let source = directory.path().join("movie.mp4");
        fs::create_dir(&base).expect("create custom base");
        fs::write(&source, b"fixture").expect("write source fixture");

        let policy = PathPolicy::default();
        assert!(policy.plan_new_project(&source, Some(&base)).is_err());
        policy
            .approve_video(&source)
            .expect("picker-approved source");
        assert!(policy.plan_new_project(&source, Some(&base)).is_err());
        policy
            .approve_project_base(&base)
            .expect("picker-approved project base");
        policy
            .plan_new_project(&source, Some(&base))
            .expect("both grants allow planning");
    }

    #[test]
    fn allocation_never_reuses_or_overwrites_colliding_entries() {
        let directory = tempdir().expect("temporary directory");
        let base = directory.path().join("projects");
        let source = directory.path().join("Movie.mp4");
        fs::create_dir(&base).expect("create custom base");
        fs::write(&source, b"fixture").expect("write source fixture");
        let first = base.join("Movie");
        fs::create_dir(&first).expect("create first collision");
        fs::write(first.join("keep.txt"), b"do not overwrite").expect("write sentinel");
        let second = base.join("Movie (2)");
        fs::write(&second, b"occupied by a file").expect("create second collision");

        let policy = PathPolicy::default();
        policy.approve_video(&source).expect("approve source");
        policy
            .approve_project_base(&base)
            .expect("approve custom base");

        let plan = policy
            .plan_new_project(&source, Some(&base))
            .expect("plan collision-free path");
        assert_eq!(plan.folder_name, "Movie (3)");
        assert_eq!(plan.project_name, "Movie (3)");
        assert_eq!(plan.collision_index, 3);
        assert!(!plan.created);
        assert!(!base.join("Movie (3)").exists());

        let allocated = policy
            .allocate_new_project(&source, Some(&base))
            .expect("allocate collision-free path");
        assert_eq!(allocated.folder_name, "Movie (3)");
        assert_eq!(allocated.collision_index, 3);
        assert!(allocated.created);
        assert!(Path::new(&allocated.project_directory).is_dir());
        assert_eq!(
            fs::read(first.join("keep.txt")).expect("read sentinel"),
            b"do not overwrite"
        );
        assert_eq!(
            fs::read(&second).expect("read occupied file"),
            b"occupied by a file"
        );

        let next = policy
            .allocate_new_project(&source, Some(&base))
            .expect("second allocation uses another child");
        assert_eq!(next.folder_name, "Movie (4)");
        assert_eq!(next.collision_index, 4);
    }

    #[test]
    fn allocation_grants_only_the_new_child_and_binds_its_source() {
        let directory = tempdir().expect("temporary directory");
        let base = directory.path().join("projects");
        let source = directory.path().join("Selected.mp4");
        let other_video = directory.path().join("Other.mp4");
        let existing_sibling = base.join("Someone Else");
        fs::create_dir_all(&existing_sibling).expect("create nonempty custom base");
        fs::write(existing_sibling.join("private.txt"), b"sibling data")
            .expect("write sibling fixture");
        fs::write(&source, b"selected").expect("write source fixture");
        fs::write(&other_video, b"other").expect("write other source fixture");

        let policy = PathPolicy::default();
        policy.approve_video(&source).expect("approve source");
        policy
            .approve_project_base(&base)
            .expect("approve custom base");
        let allocated = policy
            .allocate_new_project(&source, Some(&base))
            .expect("allocate project");
        let project = PathBuf::from(&allocated.project_directory);

        assert!(policy.resolve_allowed_directory(&project).is_ok());
        assert!(policy.resolve_allowed_directory(&base).is_err());
        assert!(policy.resolve_allowed_directory(&existing_sibling).is_err());
        assert!(policy
            .resolve_allowed_existing_any(&existing_sibling.join("private.txt"))
            .is_err());
        assert!(policy.resolve_allowed_video(&source).is_ok());
        assert!(policy.resolve_allowed_video(&other_video).is_err());

        let mut create_params = Map::from_iter([
            (
                "project_dir".to_owned(),
                Value::String(allocated.project_directory.clone()),
            ),
            (
                "input_path".to_owned(),
                Value::String(allocated.source_path.clone()),
            ),
        ]);
        validate_worker_paths(WorkerMethod::CreateProject, &mut create_params, &policy)
            .expect("pre-bound allocation is valid for project creation");

        write_project_marker(&project, &source);
        let mut open_params = Map::from_iter([(
            "project_dir".to_owned(),
            Value::String(allocated.project_directory),
        )]);
        validate_worker_paths(WorkerMethod::GetProject, &mut open_params, &policy)
            .expect("created marker preserves the exact source binding");
    }

    #[test]
    fn project_base_picker_rejects_files() {
        let directory = tempdir().expect("temporary directory");
        let file = directory.path().join("not-a-base");
        fs::write(&file, b"fixture").expect("write file fixture");
        assert!(PathPolicy::default().approve_project_base(&file).is_err());
    }

    #[test]
    fn grants_only_the_selected_video_file() {
        let directory = tempdir().expect("temporary directory");
        let selected = directory.path().join("selected.mp4");
        let sibling = directory.path().join("sibling.mp4");
        fs::write(&selected, b"fixture").expect("write selected fixture");
        fs::write(&sibling, b"fixture").expect("write sibling fixture");

        let policy = PathPolicy::default();
        policy.approve_video(&selected).expect("approve video");

        assert!(policy.resolve_allowed_video(&selected).is_ok());
        assert!(policy.resolve_allowed_video(&sibling).is_err());
    }

    #[test]
    fn project_root_grant_allows_generated_output() {
        let directory = tempdir().expect("temporary directory");
        let project = directory.path().join("project");
        fs::create_dir(&project).expect("create project");

        let policy = PathPolicy::default();
        policy.approve_root(&project).expect("approve project");

        assert!(policy
            .resolve_allowed_output(&project.join("renders").join("movie.mp4"))
            .is_err());
        fs::create_dir(project.join("renders")).expect("create renders");
        assert!(policy
            .resolve_allowed_output(&project.join("renders").join("movie.mp4"))
            .is_ok());
    }

    #[test]
    fn project_picker_rejects_nonempty_non_project_folder() {
        let directory = tempdir().expect("temporary directory");
        fs::write(directory.path().join("unrelated.txt"), b"not a project")
            .expect("write unrelated fixture");

        assert!(PathPolicy::default()
            .approve_root(directory.path())
            .is_err());
    }

    #[test]
    fn project_picker_accepts_empty_and_source_confirmed_project_folders() {
        let empty = tempdir().expect("empty temporary directory");
        PathPolicy::default()
            .approve_root(empty.path())
            .expect("empty project destination");

        let existing = tempdir().expect("project temporary directory");
        let source_directory = tempdir().expect("source temporary directory");
        let source = source_directory.path().join("source.mp4");
        fs::write(&source, b"fixture").expect("write source fixture");
        write_project_marker(existing.path(), &source);
        fs::write(existing.path().join("config.json"), b"{}").expect("write project config");
        let policy = PathPolicy::default();
        let inspection = policy
            .inspect_project_folder(existing.path())
            .expect("recognized project");
        assert_eq!(inspection.kind, ProjectFolderKind::Existing);
        assert!(policy.approve_root(existing.path()).is_err());
        policy
            .approve_existing_project(existing.path(), &source)
            .expect("source-confirmed project");
        assert!(policy.resolve_allowed_directory(existing.path()).is_ok());
        assert!(policy.resolve_allowed_video(&source).is_ok());
    }

    #[test]
    fn mismatched_source_does_not_grant_the_project_or_selected_file() {
        let project = tempdir().expect("project temporary directory");
        let sources = tempdir().expect("source temporary directory");
        let expected = sources.path().join("expected.mp4");
        let selected = sources.path().join("selected.mp4");
        fs::write(&expected, b"expected").expect("write expected source");
        fs::write(&selected, b"selected").expect("write selected source");
        write_project_marker(project.path(), &expected);

        let policy = PathPolicy::default();
        let error = policy
            .approve_existing_project(project.path(), &selected)
            .expect_err("mismatched source must fail");
        assert_eq!(
            error.message,
            "selected video does not match the source recorded by this project"
        );
        assert!(policy.resolve_allowed_directory(project.path()).is_err());
        assert!(policy.resolve_allowed_video(&selected).is_err());
    }

    #[test]
    fn project_inspection_does_not_probe_the_unselected_stored_source() {
        let project = tempdir().expect("project temporary directory");
        let sources = tempdir().expect("source temporary directory");
        let missing = sources.path().join("missing.mp4");
        let selected = sources.path().join("different.mp4");
        fs::write(&selected, b"selected").expect("write selected source");
        write_project_marker_raw(
            project.path(),
            missing.to_str().expect("Unicode missing source path"),
        );

        let policy = PathPolicy::default();
        let inspection = policy
            .inspect_project_folder(project.path())
            .expect("stored source is inspected lexically only");
        assert_eq!(inspection.kind, ProjectFolderKind::Existing);
        let error = policy
            .approve_existing_project(project.path(), &selected)
            .expect_err("different selected source must fail");
        assert_eq!(
            error.message,
            "selected video does not match the source recorded by this project"
        );
    }

    #[test]
    fn project_marker_rejects_relative_source_references() {
        let project = tempdir().expect("project temporary directory");
        write_project_marker_raw(project.path(), "..\\unselected.mp4");

        assert!(PathPolicy::default()
            .inspect_project_folder(project.path())
            .is_err());
    }

    #[test]
    fn project_marker_name_limit_counts_unicode_characters_not_bytes() {
        let project = tempdir().expect("project temporary directory");
        let sources = tempdir().expect("source temporary directory");
        let source = sources.path().join("source.mp4");
        fs::write(&source, b"fixture").expect("write source fixture");
        let source = canonical_existing_file(&source).expect("canonical source fixture");

        let marker = serde_json::json!({
            "schema_version": "1.0",
            "name": "\u{1f3ac}".repeat(160),
            "input_path": path_to_string(&source).expect("Unicode source path"),
        });
        fs::write(
            project.path().join("project.json"),
            serde_json::to_vec(&marker).expect("serialize Unicode marker"),
        )
        .expect("write Unicode marker");
        PathPolicy::default()
            .inspect_project_folder(project.path())
            .expect("160 Unicode characters are valid");

        let oversized = serde_json::json!({
            "schema_version": "1.0",
            "name": "\u{1f3ac}".repeat(161),
            "input_path": path_to_string(&source).expect("Unicode source path"),
        });
        fs::write(
            project.path().join("project.json"),
            serde_json::to_vec(&oversized).expect("serialize oversized marker"),
        )
        .expect("write oversized marker");
        assert!(PathPolicy::default()
            .inspect_project_folder(project.path())
            .is_err());
    }

    #[test]
    fn project_marker_symlink_is_not_followed() {
        let project = tempdir().expect("project temporary directory");
        let outside = tempdir().expect("outside temporary directory");
        let source = outside.path().join("source.mp4");
        fs::write(&source, b"fixture").expect("write source fixture");
        write_project_marker(outside.path(), &source);
        let result = create_file_symlink(
            &outside.path().join("project.json"),
            &project.path().join("project.json"),
        );
        if let Err(error) = result {
            if matches!(
                error.kind(),
                io::ErrorKind::PermissionDenied | io::ErrorKind::Unsupported
            ) {
                return;
            }
            panic!("could not create symlink fixture: {error}");
        }

        assert!(PathPolicy::default()
            .inspect_project_folder(project.path())
            .is_err());
    }

    #[test]
    fn depth_manifest_rejects_relative_and_absolute_artifact_escapes() {
        for escaped in ["../outside.npz", "depth/../outside.npz"] {
            let project = tempdir().expect("project temporary directory");
            let sources = tempdir().expect("source temporary directory");
            let source = sources.path().join("source.mp4");
            fs::write(&source, b"fixture").expect("write source fixture");
            write_project_marker(project.path(), &source);
            fs::create_dir(project.path().join("depth")).expect("create depth directory");
            let manifest = serde_json::json!({
                "schema_version": "1.0",
                "backend": "cached",
                "shots": [{"path": escaped}],
            });
            fs::write(
                project.path().join("depth/metadata.json"),
                serde_json::to_vec(&manifest).expect("serialize depth manifest"),
            )
            .expect("write depth manifest");
            assert!(PathPolicy::default()
                .inspect_project_folder(project.path())
                .is_err());
        }

        let project = tempdir().expect("project temporary directory");
        let sources = tempdir().expect("source temporary directory");
        let source = sources.path().join("source.mp4");
        let outside = sources.path().join("outside.npz");
        fs::write(&source, b"fixture").expect("write source fixture");
        write_project_marker(project.path(), &source);
        fs::create_dir(project.path().join("depth")).expect("create depth directory");
        let manifest = serde_json::json!({
            "schema_version": "1.0",
            "backend": "cached",
            "shots": [{"path": outside.to_str().expect("Unicode outside path")}],
        });
        fs::write(
            project.path().join("depth/metadata.json"),
            serde_json::to_vec(&manifest).expect("serialize depth manifest"),
        )
        .expect("write depth manifest");
        assert!(PathPolicy::default()
            .inspect_project_folder(project.path())
            .is_err());
    }

    #[test]
    fn source_confirmed_project_accepts_contained_depth_artifacts() {
        let project = tempdir().expect("project temporary directory");
        let sources = tempdir().expect("source temporary directory");
        let source = sources.path().join("source.mp4");
        fs::write(&source, b"fixture").expect("write source fixture");
        write_project_marker(project.path(), &source);
        fs::create_dir(project.path().join("depth")).expect("create depth directory");
        fs::write(project.path().join("depth/shot_0001.npz"), b"fixture")
            .expect("write depth fixture");
        let manifest = serde_json::json!({
            "schema_version": "1.0",
            "backend": "cached",
            "shots": [{"path": "depth/shot_0001.npz"}],
        });
        fs::write(
            project.path().join("depth/metadata.json"),
            serde_json::to_vec(&manifest).expect("serialize depth manifest"),
        )
        .expect("write depth manifest");

        PathPolicy::default()
            .approve_existing_project(project.path(), &source)
            .expect("contained project artifacts");
    }

    #[test]
    fn worker_requests_recheck_the_confirmed_project_source() {
        let project = tempdir().expect("project temporary directory");
        let sources = tempdir().expect("source temporary directory");
        let confirmed = sources.path().join("confirmed.mp4");
        let replacement = sources.path().join("replacement.mp4");
        fs::write(&confirmed, b"confirmed").expect("write confirmed source");
        fs::write(&replacement, b"replacement").expect("write replacement source");
        write_project_marker(project.path(), &confirmed);

        let policy = PathPolicy::default();
        policy
            .approve_existing_project(project.path(), &confirmed)
            .expect("confirm existing project");
        let mut params = Map::from_iter([(
            "project_dir".to_owned(),
            Value::String(path_to_string(project.path()).expect("Unicode project path")),
        )]);
        validate_worker_paths(WorkerMethod::GetProject, &mut params, &policy)
            .expect("unchanged project source");

        write_project_marker(project.path(), &replacement);
        let error = validate_worker_paths(WorkerMethod::GetProject, &mut params, &policy)
            .expect_err("changed project source must fail");
        assert_eq!(
            error.message,
            "project source reference changed after native confirmation"
        );
    }

    #[test]
    fn worker_requests_recheck_artifact_containment_after_approval() {
        let project = tempdir().expect("project temporary directory");
        let sources = tempdir().expect("source temporary directory");
        let source = sources.path().join("source.mp4");
        fs::write(&source, b"fixture").expect("write source fixture");
        write_project_marker(project.path(), &source);

        let policy = PathPolicy::default();
        policy
            .approve_existing_project(project.path(), &source)
            .expect("confirm existing project");
        fs::create_dir(project.path().join("depth")).expect("create depth directory");
        let escaped = serde_json::json!({
            "schema_version": "1.0",
            "backend": "cached",
            "shots": [{"path": "../outside.npz"}],
        });
        fs::write(
            project.path().join("depth/metadata.json"),
            serde_json::to_vec(&escaped).expect("serialize depth manifest"),
        )
        .expect("write escaped depth manifest");
        let mut params = Map::from_iter([(
            "project_dir".to_owned(),
            Value::String(path_to_string(project.path()).expect("Unicode project path")),
        )]);

        assert!(validate_worker_paths(WorkerMethod::GetProject, &mut params, &policy).is_err());
    }

    #[test]
    fn create_project_request_binds_two_picker_granted_paths() {
        let project = tempdir().expect("project temporary directory");
        let sources = tempdir().expect("source temporary directory");
        let source = sources.path().join("source.mp4");
        fs::write(&source, b"fixture").expect("write source fixture");
        let policy = PathPolicy::default();
        policy
            .approve_root(project.path())
            .expect("approve empty project");
        policy.approve_video(&source).expect("approve source video");
        let mut create_params = Map::from_iter([
            (
                "project_dir".to_owned(),
                Value::String(path_to_string(project.path()).expect("Unicode project path")),
            ),
            (
                "input_path".to_owned(),
                Value::String(path_to_string(&source).expect("Unicode source path")),
            ),
        ]);
        validate_worker_paths(WorkerMethod::CreateProject, &mut create_params, &policy)
            .expect("bind new project source");

        write_project_marker(project.path(), &source);
        let mut open_params = Map::from_iter([(
            "project_dir".to_owned(),
            Value::String(path_to_string(project.path()).expect("Unicode project path")),
        )]);
        validate_worker_paths(WorkerMethod::GetProject, &mut open_params, &policy)
            .expect("bound project can be reopened in the same session");
    }

    #[test]
    fn cache_candidate_must_be_below_approved_project() {
        let directory = tempdir().expect("temporary directory");
        let project = directory.path().join("project");
        fs::create_dir(&project).expect("create project");
        let policy = PathPolicy::default();
        policy.approve_root(&project).expect("approve project");

        assert!(policy
            .resolve_allowed_directory_candidate(&project.join("depth-cache"))
            .is_ok());
        assert!(policy
            .resolve_allowed_directory_candidate(&directory.path().join("outside-cache"))
            .is_err());
    }

    #[test]
    fn preview_scope_allows_media_but_not_project_json() {
        let directory = tempdir().expect("temporary directory");
        let policy = PathPolicy::default();
        policy
            .approve_root(directory.path())
            .expect("approve empty project");
        let preview = directory.path().join("preview.mp4");
        let manifest = directory.path().join("project.json");
        fs::write(&preview, b"fixture").expect("write preview");
        fs::write(&manifest, b"{}").expect("write manifest");

        assert!(policy.resolve_allowed_preview_file(&preview).is_ok());
        assert!(policy.resolve_allowed_preview_file(&manifest).is_err());
    }

    #[test]
    fn configuration_files_have_a_narrow_extension_allowlist() {
        let directory = tempdir().expect("temporary directory");
        let policy = PathPolicy::default();
        policy
            .approve_root(directory.path())
            .expect("approve empty project");
        let config = directory.path().join("config.json");
        let executable = directory.path().join("config.exe");
        fs::write(&config, b"{}").expect("write config");
        fs::write(&executable, b"fixture").expect("write executable");

        assert!(policy.resolve_allowed_config_file(&config).is_ok());
        assert!(policy.resolve_allowed_config_file(&executable).is_err());
    }
}
