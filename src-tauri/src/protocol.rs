use crate::error::{BridgeError, BridgeResult};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::HashSet;

pub const MAX_REQUEST_BYTES: usize = 256 * 1024;
pub const MAX_EVENT_BYTES: usize = 1024 * 1024;
const MAX_IDENTIFIER_BYTES: usize = 128;

/// The webview can call product operations only. It cannot select an executable,
/// provide command-line arguments, or invoke a general-purpose shell method.
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum WorkerMethod {
    Ping,
    CreateProject,
    Inspect,
    Normalize,
    DetectShots,
    AnalyzeDraft,
    EstimateDepth,
    ExtractFeatures,
    CreateStereoScript,
    Direct,
    RenderPreview,
    RenderPreviewFrame,
    RenderFinal,
    Render,
    GenerateQc,
    Qc,
    RunPipeline,
    Run,
    GetProject,
    LlmStatus,
    TestLlm,
    RecommendPreset,
    ApplyShotOverrides,
    Cancel,
    CancelJob,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerRequest {
    pub id: String,
    pub method: WorkerMethod,
    pub params: Map<String, Value>,
}

impl WorkerRequest {
    pub fn validate(&self) -> BridgeResult<()> {
        validate_request_identifier(self.method, &self.id)?;

        let encoded_size = serde_json::to_vec(self)
            .map_err(|_| BridgeError::invalid("request is not valid JSON"))?
            .len();
        if encoded_size > MAX_REQUEST_BYTES {
            return Err(BridgeError::invalid(format!(
                "request exceeds the {MAX_REQUEST_BYTES}-byte IPC limit"
            )));
        }

        validate_value_tree(&Value::Object(self.params.clone()), 0)?;
        validate_method_params(self.method, &self.params)
    }
}

fn validate_method_params(method: WorkerMethod, params: &Map<String, Value>) -> BridgeResult<()> {
    reject_secret_parameter_names(&Value::Object(params.clone()))?;

    let (allowed, required) = parameter_contract(method);
    if params.keys().any(|key| !allowed.contains(&key.as_str())) {
        return Err(BridgeError::invalid(
            "request contains a parameter that is not allowed for this operation",
        ));
    }
    if required.iter().any(|key| !params.contains_key(*key)) {
        return Err(BridgeError::invalid(
            "request is missing a required parameter for this operation",
        ));
    }

    if method == WorkerMethod::Inspect {
        let has_project = params.contains_key("project_dir");
        let has_input = params.contains_key("input_path");
        if has_project == has_input || (has_input && params.len() != 1) {
            return Err(BridgeError::invalid(
                "inspect requires either input_path alone or a project_dir",
            ));
        }
    }

    for (key, value) in params {
        match key.as_str() {
            "project_dir" | "input_path" | "output_path" | "cache_dir" => {
                require_nonempty_string(value, 32 * 1024)?;
            }
            "name" => require_project_name(value)?,
            "profile" => require_one_of(value, &["representative_frames"])?,
            "depth_backend" | "backend" => require_one_of(
                value,
                &[
                    "synthetic",
                    "cached",
                    "monocular-cues",
                    "monocular",
                    "image-analysis",
                    "video-depth-anything-small",
                    "vda-small",
                    "video-depth-anything",
                ],
            )?,
            "device" => require_one_of(value, &["auto", "cpu", "cuda", "mps"])?,
            "anaglyph_mode" => require_one_of(value, &["basic", "calibrated"])?,
            "output_mode" => require_one_of(value, &["anaglyph", "left", "right", "side-by-side"])?,
            "director" => require_one_of(value, &["rules", "llm"])?,
            "swap_eyes" | "llm_enabled" | "resume" | "allow_fallback" => {
                if !value.is_boolean() {
                    return Err(BridgeError::invalid(
                        "request contains a parameter with the wrong type",
                    ));
                }
            }
            "shot_id" => require_positive_integer(value)?,
            "frame_offset" => require_frame_index(value)?,
            "preview_max_width" => require_preview_width(value)?,
            "force_stages" => validate_force_stages(value)?,
            "speech_intervals" => validate_speech_intervals(value)?,
            "expected_revision" => validate_revision(value)?,
            "overrides" => validate_shot_overrides(value)?,
            "features" => validate_shot_features(value)?,
            "model" => require_one_of(value, &["gpt-5.6-terra"])?,
            "job_id" => {
                let id = value.as_str().ok_or_else(|| {
                    BridgeError::invalid("request contains a parameter with the wrong type")
                })?;
                validate_app_generated_identifier("job id", id)?;
            }
            _ => {
                return Err(BridgeError::invalid(
                    "request contains an unsupported parameter",
                ))
            }
        }
    }
    Ok(())
}

fn parameter_contract(method: WorkerMethod) -> (&'static [&'static str], &'static [&'static str]) {
    use WorkerMethod::*;
    match method {
        Ping => (&[], &[]),
        LlmStatus | TestLlm => (&["model"], &[]),
        RecommendPreset => (&["features", "model"], &["features"]),
        ApplyShotOverrides => (
            &["project_dir", "expected_revision", "overrides"],
            &["project_dir", "expected_revision", "overrides"],
        ),
        Cancel | CancelJob => (&["job_id"], &["job_id"]),
        CreateProject => (
            &[
                "input_path",
                "project_dir",
                "name",
                "depth_backend",
                "device",
                "anaglyph_mode",
                "swap_eyes",
                "preview_max_width",
                "llm_enabled",
                "model",
            ],
            &["input_path", "project_dir"],
        ),
        Inspect => (
            &["input_path", "project_dir", "resume", "force_stages"],
            &[],
        ),
        AnalyzeDraft => (
            &[
                "project_dir",
                "resume",
                "force_stages",
                "depth_backend",
                "device",
                "profile",
                "allow_fallback",
            ],
            &["project_dir"],
        ),
        EstimateDepth => (
            &[
                "project_dir",
                "resume",
                "force_stages",
                "depth_backend",
                "device",
                "backend",
                "cache_dir",
                "allow_fallback",
            ],
            &["project_dir"],
        ),
        ExtractFeatures => (
            &[
                "project_dir",
                "resume",
                "force_stages",
                "depth_backend",
                "device",
                "speech_intervals",
                "allow_fallback",
            ],
            &["project_dir"],
        ),
        CreateStereoScript | Direct => (
            &[
                "project_dir",
                "resume",
                "force_stages",
                "depth_backend",
                "device",
                "director",
                "llm_enabled",
                "model",
                "allow_fallback",
            ],
            &["project_dir"],
        ),
        RenderPreview => (
            &[
                "project_dir",
                "resume",
                "force_stages",
                "shot_id",
                "output_path",
                "output_mode",
                "depth_backend",
                "device",
                "anaglyph_mode",
                "swap_eyes",
                "preview_max_width",
                "allow_fallback",
            ],
            &["project_dir", "shot_id"],
        ),
        RenderPreviewFrame => (
            &[
                "project_dir",
                "resume",
                "force_stages",
                "shot_id",
                "frame_offset",
                "output_path",
                "output_mode",
                "depth_backend",
                "device",
                "anaglyph_mode",
                "swap_eyes",
                "preview_max_width",
                "allow_fallback",
            ],
            &["project_dir", "shot_id"],
        ),
        RenderFinal | Render => (
            &[
                "project_dir",
                "resume",
                "force_stages",
                "output_path",
                "output_mode",
                "depth_backend",
                "device",
                "anaglyph_mode",
                "swap_eyes",
                "preview_max_width",
            ],
            &["project_dir"],
        ),
        RunPipeline | Run => (
            &[
                "project_dir",
                "resume",
                "force_stages",
                "output_path",
                "output_mode",
                "director",
                "depth_backend",
                "device",
                "anaglyph_mode",
                "swap_eyes",
                "preview_max_width",
                "llm_enabled",
                "model",
            ],
            &["project_dir"],
        ),
        Normalize | DetectShots | GenerateQc | Qc | GetProject => {
            (&["project_dir", "resume", "force_stages"], &["project_dir"])
        }
    }
}

fn reject_secret_parameter_names(value: &Value) -> BridgeResult<()> {
    match value {
        Value::Object(values) => {
            for (key, value) in values {
                let normalized: String = key
                    .chars()
                    .filter(|character| character.is_ascii_alphanumeric())
                    .flat_map(char::to_lowercase)
                    .collect();
                if [
                    "apikey",
                    "authorization",
                    "accesstoken",
                    "refreshtoken",
                    "password",
                    "credential",
                    "bearer",
                    "secret",
                    "token",
                ]
                .iter()
                .any(|marker| normalized.contains(marker))
                {
                    return Err(BridgeError::invalid(
                        "secrets must use the native credential command, never worker parameters",
                    ));
                }
                reject_secret_parameter_names(value)?;
            }
        }
        Value::Array(values) => {
            for value in values {
                reject_secret_parameter_names(value)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn require_nonempty_string(value: &Value, maximum: usize) -> BridgeResult<()> {
    match value.as_str() {
        Some(value) if !value.is_empty() && value.len() <= maximum => Ok(()),
        _ => Err(BridgeError::invalid(
            "request contains an invalid string parameter",
        )),
    }
}

fn require_project_name(value: &Value) -> BridgeResult<()> {
    match value.as_str() {
        Some(value)
            if !value.trim().is_empty()
                && value.chars().count() <= 160
                && !value.chars().any(char::is_control) =>
        {
            Ok(())
        }
        _ => Err(BridgeError::invalid(
            "request contains an invalid project name",
        )),
    }
}

fn require_one_of(value: &Value, allowed: &[&str]) -> BridgeResult<()> {
    match value.as_str() {
        Some(value) if allowed.contains(&value) => Ok(()),
        _ => Err(BridgeError::invalid(
            "request contains a value outside the supported vocabulary",
        )),
    }
}

fn require_positive_integer(value: &Value) -> BridgeResult<()> {
    match value.as_u64() {
        Some(value) if (1..=1_000_000_000).contains(&value) => Ok(()),
        _ => Err(BridgeError::invalid(
            "request contains an invalid positive integer",
        )),
    }
}

fn require_preview_width(value: &Value) -> BridgeResult<()> {
    match value.as_u64() {
        Some(value) if (160..=3840).contains(&value) => Ok(()),
        _ => Err(BridgeError::invalid(
            "request contains an invalid preview width",
        )),
    }
}

fn require_frame_index(value: &Value) -> BridgeResult<()> {
    match value.as_u64() {
        Some(value) if value <= 100_000_000 => Ok(()),
        _ => Err(BridgeError::invalid(
            "request contains an invalid frame index",
        )),
    }
}

fn require_finite_number(value: &Value, minimum: f64, maximum: f64) -> BridgeResult<()> {
    match value.as_f64() {
        Some(value) if value.is_finite() && value >= minimum && value <= maximum => Ok(()),
        _ => Err(BridgeError::invalid(
            "request contains an out-of-range numeric parameter",
        )),
    }
}

fn validate_revision(value: &Value) -> BridgeResult<()> {
    match value.as_str() {
        Some(value)
            if value.len() == 64
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) =>
        {
            Ok(())
        }
        _ => Err(BridgeError::invalid("script revision is not valid")),
    }
}

fn validate_analyze_draft_result(value: &Value) -> BridgeResult<()> {
    const RESULT_KEYS: &[&str] = &[
        "analysis_tier",
        "profile",
        "features",
        "script",
        "revision",
        "coverage",
    ];
    let result = value
        .as_object()
        .filter(|result| {
            result.len() == RESULT_KEYS.len()
                && result.keys().all(|key| RESULT_KEYS.contains(&key.as_str()))
        })
        .ok_or_else(|| BridgeError::invalid("analyze_draft result has an invalid shape"))?;
    require_one_of(&result["analysis_tier"], &["sampled"])?;
    require_one_of(&result["profile"], &["representative_frames"])?;
    if !result["features"].is_object() || !result["script"].is_object() {
        return Err(BridgeError::invalid(
            "analyze_draft features and script must be objects",
        ));
    }
    validate_revision(&result["revision"])?;
    validate_draft_coverage(&result["coverage"])
}

fn validate_draft_coverage(value: &Value) -> BridgeResult<()> {
    const REQUIRED_KEYS: &[&str] = &["shot_ids", "sampled_frames", "total_frames", "per_shot"];
    let coverage = value
        .as_object()
        .filter(|coverage| {
            REQUIRED_KEYS.iter().all(|key| coverage.contains_key(*key))
                && coverage
                    .keys()
                    .all(|key| REQUIRED_KEYS.contains(&key.as_str()))
                && coverage.len() == REQUIRED_KEYS.len()
        })
        .ok_or_else(|| BridgeError::invalid("analyze_draft coverage has an invalid shape"))?;
    let shot_ids = coverage["shot_ids"]
        .as_array()
        .filter(|shot_ids| shot_ids.len() <= 10_000)
        .ok_or_else(|| BridgeError::invalid("analyze_draft shot_ids must be a bounded array"))?;
    let mut unique_ids = HashSet::with_capacity(shot_ids.len());
    for shot_id in shot_ids {
        require_positive_integer(shot_id)?;
        let shot_id = shot_id.as_u64().expect("positive integer was validated");
        if !unique_ids.insert(shot_id) {
            return Err(BridgeError::invalid(
                "analyze_draft shot_ids must be unique",
            ));
        }
    }
    let sampled_frames = coverage["sampled_frames"]
        .as_u64()
        .ok_or_else(|| BridgeError::invalid("analyze_draft sampled_frames must be an integer"))?;
    let total_frames = coverage["total_frames"]
        .as_u64()
        .ok_or_else(|| BridgeError::invalid("analyze_draft total_frames must be an integer"))?;
    if sampled_frames > total_frames {
        return Err(BridgeError::invalid(
            "analyze_draft sampled coverage exceeds total frames",
        ));
    }

    let per_shot = coverage["per_shot"]
        .as_array()
        .filter(|per_shot| per_shot.len() == unique_ids.len())
        .ok_or_else(|| BridgeError::invalid("analyze_draft per_shot coverage is incomplete"))?;
    let mut per_shot_ids = HashSet::with_capacity(per_shot.len());
    let mut sampled_sum = 0_u64;
    let mut total_sum = 0_u64;
    for entry in per_shot {
        const ENTRY_KEYS: &[&str] = &["shot_id", "sampled_frames", "total_frames"];
        let entry = entry
            .as_object()
            .filter(|entry| {
                entry.len() == ENTRY_KEYS.len()
                    && entry.keys().all(|key| ENTRY_KEYS.contains(&key.as_str()))
            })
            .ok_or_else(|| BridgeError::invalid("analyze_draft per_shot entry is invalid"))?;
        require_positive_integer(&entry["shot_id"])?;
        let shot_id = entry["shot_id"]
            .as_u64()
            .expect("positive integer was validated");
        if !unique_ids.contains(&shot_id) || !per_shot_ids.insert(shot_id) {
            return Err(BridgeError::invalid(
                "analyze_draft per_shot identifiers do not match shot_ids",
            ));
        }
        let sampled = entry["sampled_frames"]
            .as_u64()
            .ok_or_else(|| BridgeError::invalid("per-shot sampled_frames must be an integer"))?;
        let total = entry["total_frames"]
            .as_u64()
            .ok_or_else(|| BridgeError::invalid("per-shot total_frames must be an integer"))?;
        if sampled > total {
            return Err(BridgeError::invalid(
                "per-shot sampled coverage exceeds total frames",
            ));
        }
        sampled_sum = sampled_sum
            .checked_add(sampled)
            .ok_or_else(|| BridgeError::invalid("sampled frame coverage overflowed"))?;
        total_sum = total_sum
            .checked_add(total)
            .ok_or_else(|| BridgeError::invalid("total frame coverage overflowed"))?;
    }
    if sampled_sum != sampled_frames || total_sum != total_frames {
        return Err(BridgeError::invalid(
            "analyze_draft aggregate coverage does not match per_shot coverage",
        ));
    }
    Ok(())
}

fn validate_force_stages(value: &Value) -> BridgeResult<()> {
    let stages = value
        .as_array()
        .ok_or_else(|| BridgeError::invalid("force_stages must be an array"))?;
    if stages.len() > 16 {
        return Err(BridgeError::invalid("too many forced stages"));
    }
    for stage in stages {
        require_one_of(
            stage,
            &[
                "inspect",
                "normalize",
                "detect_shots",
                "analyze_draft",
                "estimate_depth",
                "extract_features",
                "direct",
                "render_preview",
                "render_final",
                "qc",
            ],
        )?;
    }
    Ok(())
}

fn validate_speech_intervals(value: &Value) -> BridgeResult<()> {
    let intervals = value
        .as_array()
        .ok_or_else(|| BridgeError::invalid("speech_intervals must be an array"))?;
    if intervals.len() > 100_000 {
        return Err(BridgeError::invalid("too many speech intervals"));
    }
    for interval in intervals {
        let pair = interval
            .as_array()
            .filter(|pair| pair.len() == 2)
            .ok_or_else(|| BridgeError::invalid("speech interval must contain start and end"))?;
        let start = pair[0]
            .as_f64()
            .filter(|value| value.is_finite() && *value >= 0.0)
            .ok_or_else(|| BridgeError::invalid("speech interval start is invalid"))?;
        let end = pair[1]
            .as_f64()
            .filter(|value| value.is_finite() && *value >= start)
            .ok_or_else(|| BridgeError::invalid("speech interval end is invalid"))?;
        if end > 7.0 * 24.0 * 60.0 * 60.0 {
            return Err(BridgeError::invalid("speech interval is too large"));
        }
    }
    Ok(())
}

fn validate_shot_features(value: &Value) -> BridgeResult<()> {
    let features = value
        .as_object()
        .ok_or_else(|| BridgeError::invalid("features must be an object"))?;
    const REQUIRED: &[&str] = &[
        "shot_id",
        "duration_seconds",
        "motion_score",
        "speech_ratio",
        "depth_spread",
        "foreground_ratio",
        "brightness",
        "cut_frequency_context",
    ];
    const OPTIONAL: &[&str] = &["camera_movement", "depth_reliability"];
    if features
        .keys()
        .any(|key| !REQUIRED.contains(&key.as_str()) && !OPTIONAL.contains(&key.as_str()))
        || REQUIRED.iter().any(|key| !features.contains_key(*key))
    {
        return Err(BridgeError::invalid(
            "features do not match the supported schema",
        ));
    }
    require_positive_integer(&features["shot_id"])?;
    require_finite_number(&features["duration_seconds"], 0.0, 7.0 * 24.0 * 60.0 * 60.0)?;
    for key in [
        "motion_score",
        "speech_ratio",
        "depth_spread",
        "foreground_ratio",
        "brightness",
        "cut_frequency_context",
    ] {
        require_finite_number(&features[key], 0.0, 1.0)?;
    }
    if let Some(value) = features.get("camera_movement") {
        require_one_of(
            value,
            &["static", "lateral", "vertical", "zoom", "unstable"],
        )?;
    }
    if let Some(value) = features.get("depth_reliability") {
        require_finite_number(value, 0.0, 1.0)?;
    }
    Ok(())
}

fn validate_shot_overrides(value: &Value) -> BridgeResult<()> {
    let overrides = value
        .as_array()
        .filter(|values| !values.is_empty() && values.len() <= 10_000)
        .ok_or_else(|| BridgeError::invalid("overrides must be a non-empty bounded array"))?;
    let mut seen = std::collections::HashSet::new();
    for value in overrides {
        let item = value
            .as_object()
            .ok_or_else(|| BridgeError::invalid("shot override must be an object"))?;
        if item.len() != 3
            || !item.contains_key("shot_id")
            || !item.contains_key("preset")
            || !item.contains_key("parameters")
        {
            return Err(BridgeError::invalid(
                "shot override does not match the supported schema",
            ));
        }
        require_positive_integer(&item["shot_id"])?;
        let shot_id = item["shot_id"]
            .as_u64()
            .expect("validated positive integer");
        if !seen.insert(shot_id) {
            return Err(BridgeError::invalid(
                "shot overrides contain a duplicate id",
            ));
        }
        require_one_of(
            &item["preset"],
            &[
                "dialogue_subtle",
                "action_controlled",
                "vista_deep",
                "closeup_flat",
                "neutral",
            ],
        )?;
        validate_stereo_parameters(&item["parameters"])?;
    }
    Ok(())
}

fn validate_stereo_parameters(value: &Value) -> BridgeResult<()> {
    let parameters = value
        .as_object()
        .ok_or_else(|| BridgeError::invalid("stereo parameters must be an object"))?;
    const KEYS: &[&str] = &[
        "depth_strength",
        "convergence_depth_percentile",
        "max_background_disparity_norm",
        "max_popout_disparity_norm",
        "temporal_smoothing",
        "transition_frames",
        "edge_protection",
    ];
    if parameters.len() != KEYS.len() || parameters.keys().any(|key| !KEYS.contains(&key.as_str()))
    {
        return Err(BridgeError::invalid(
            "stereo parameters do not match the supported schema",
        ));
    }
    require_finite_number(&parameters["depth_strength"], 0.0, 2.0)?;
    require_finite_number(&parameters["convergence_depth_percentile"], 0.0, 1.0)?;
    require_finite_number(&parameters["max_background_disparity_norm"], 0.0, 0.05)?;
    require_finite_number(&parameters["max_popout_disparity_norm"], 0.0, 0.05)?;
    require_finite_number(&parameters["temporal_smoothing"], 0.0, 1.0)?;
    match parameters["transition_frames"].as_u64() {
        Some(value) if value <= 1_000 => {}
        _ => return Err(BridgeError::invalid("transition_frames is invalid")),
    }
    if !parameters["edge_protection"].is_boolean() {
        return Err(BridgeError::invalid("edge_protection must be a boolean"));
    }
    Ok(())
}

fn validate_request_identifier(method: WorkerMethod, value: &str) -> BridgeResult<()> {
    validate_app_generated_identifier("request id", value)?;
    let prefix = identifier_prefix(value).expect("app identifier has a UUID suffix");
    if !request_prefixes(method).contains(&prefix) {
        return Err(BridgeError::invalid(
            "request id prefix does not match the requested operation",
        ));
    }
    Ok(())
}

fn request_prefixes(method: WorkerMethod) -> &'static [&'static str] {
    use WorkerMethod::*;
    match method {
        Ping => &["ping"],
        CreateProject => &["create-project"],
        Inspect => &["inspect"],
        Normalize => &["normalize"],
        DetectShots => &["detect-shots"],
        AnalyzeDraft => &["analyze-draft"],
        EstimateDepth => &["estimate-depth"],
        ExtractFeatures => &["extract-features"],
        CreateStereoScript => &["create-stereo-script"],
        Direct => &["direct"],
        RenderPreview => &["preview"],
        RenderPreviewFrame => &["preview-frame"],
        RenderFinal => &["export"],
        Render => &["render"],
        GenerateQc => &["generate-qc"],
        Qc => &["qc"],
        RunPipeline => &["run-pipeline"],
        Run => &["run"],
        GetProject => &["open-project"],
        LlmStatus => &["llm-status"],
        TestLlm => &["llm-test"],
        RecommendPreset => &["recommend"],
        ApplyShotOverrides => &["save-script"],
        Cancel | CancelJob => &["cancel"],
    }
}

const APP_IDENTIFIER_PREFIXES: &[&str] = &[
    "ping",
    "create-project",
    "inspect",
    "normalize",
    "detect-shots",
    "analyze-draft",
    "estimate-depth",
    "extract-features",
    "create-stereo-script",
    "direct",
    "preview",
    "preview-frame",
    "export",
    "render",
    "generate-qc",
    "qc",
    "run-pipeline",
    "run",
    "open-project",
    "llm-status",
    "llm-test",
    "recommend",
    "save-script",
    "cancel",
];

pub fn validate_app_generated_identifier(label: &str, value: &str) -> BridgeResult<()> {
    validate_identifier(label, value)?;
    let Some(prefix) = identifier_prefix(value) else {
        return Err(BridgeError::invalid(format!(
            "{label} must end with a canonical UUID v4"
        )));
    };
    if !APP_IDENTIFIER_PREFIXES.contains(&prefix) {
        return Err(BridgeError::invalid(format!(
            "{label} has an unsupported operation prefix"
        )));
    }
    Ok(())
}

fn identifier_prefix(value: &str) -> Option<&str> {
    const UUID_BYTES: usize = 36;
    if value.len() <= UUID_BYTES {
        return None;
    }
    let separator = value.len() - UUID_BYTES - 1;
    if value.as_bytes().get(separator) != Some(&b'-') {
        return None;
    }
    let uuid = value.get(separator + 1..)?;
    if !is_canonical_uuid_v4(uuid) {
        return None;
    }
    value.get(..separator)
}

fn is_canonical_uuid_v4(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() != 36
        || bytes[8] != b'-'
        || bytes[13] != b'-'
        || bytes[18] != b'-'
        || bytes[23] != b'-'
        || bytes[14] != b'4'
        || !matches!(bytes[19], b'8' | b'9' | b'a' | b'b')
    {
        return false;
    }
    bytes.iter().enumerate().all(|(index, byte)| {
        matches!(index, 8 | 13 | 18 | 23) || byte.is_ascii_digit() || (b'a'..=b'f').contains(byte)
    })
}

pub fn validate_identifier(label: &str, value: &str) -> BridgeResult<()> {
    if value.is_empty() || value.len() > MAX_IDENTIFIER_BYTES {
        return Err(BridgeError::invalid(format!(
            "{label} must contain between 1 and {MAX_IDENTIFIER_BYTES} bytes"
        )));
    }
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':'))
    {
        return Err(BridgeError::invalid(format!(
            "{label} contains unsupported characters"
        )));
    }
    Ok(())
}

fn validate_value_tree(value: &Value, depth: usize) -> BridgeResult<()> {
    if depth > 20 {
        return Err(BridgeError::invalid(
            "request parameters are nested too deeply",
        ));
    }

    match value {
        Value::String(value) if value.len() > 32 * 1024 => {
            Err(BridgeError::invalid("a request string exceeds 32 KiB"))
        }
        Value::String(value) if value.contains('\0') => Err(BridgeError::invalid(
            "request strings must not contain NUL bytes",
        )),
        Value::Array(values) => {
            for value in values {
                validate_value_tree(value, depth + 1)?;
            }
            Ok(())
        }
        Value::Object(values) => {
            for (key, value) in values {
                if key.len() > 128 || key.chars().any(char::is_control) {
                    return Err(BridgeError::invalid(
                        "request contains an invalid object key",
                    ));
                }
                validate_value_tree(value, depth + 1)?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerAck {
    pub accepted: bool,
    pub id: String,
    pub worker_pid: u32,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum WorkerEvent {
    Progress {
        id: String,
        job_id: String,
        stage: String,
        completed: u64,
        total: u64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        message: Option<String>,
    },
    Result {
        id: String,
        result: Value,
    },
    Error {
        id: String,
        error: WorkerErrorBody,
    },
    Log {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        id: Option<String>,
        level: LogLevel,
        message: String,
    },
}

impl WorkerEvent {
    pub fn lifecycle_error(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::Error {
            id: "worker-lifecycle".to_owned(),
            error: WorkerErrorBody {
                code: code.into(),
                message: message.into(),
                details: None,
                retryable: true,
            },
        }
    }

    pub fn protocol_error(
        id: impl Into<String>,
        code: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self::Error {
            id: id.into(),
            error: WorkerErrorBody {
                code: code.into(),
                message: message.into(),
                details: None,
                retryable: false,
            },
        }
    }

    pub fn host_log(level: LogLevel, message: impl Into<String>) -> Self {
        Self::Log {
            id: None,
            level,
            message: message.into(),
        }
    }

    pub fn validate(&self) -> BridgeResult<()> {
        match self {
            Self::Progress {
                id,
                job_id,
                stage,
                completed,
                total,
                message,
                ..
            } => {
                validate_event_identifier("event id", id)?;
                validate_app_generated_identifier("job id", job_id)?;
                if stage.is_empty() || stage.len() > 128 || stage.chars().any(char::is_control) {
                    return Err(BridgeError::worker("worker emitted an invalid stage name"));
                }
                if *completed > *total && *total != 0 {
                    return Err(BridgeError::worker(
                        "worker progress completed value exceeds total",
                    ));
                }
                validate_optional_message(message.as_deref())
            }
            Self::Result { id, result } => {
                validate_event_identifier("event id", id)?;
                reject_secret_parameter_names(result)
                    .map_err(|_| BridgeError::worker("worker result contained a secret field"))?;
                if identifier_prefix(id) == Some("analyze-draft") {
                    validate_analyze_draft_result(result).map_err(|error| {
                        BridgeError::worker(format!(
                            "worker emitted an invalid analyze_draft result: {}",
                            error.message
                        ))
                    })?;
                }
                Ok(())
            }
            Self::Error { id, error } => {
                validate_event_identifier("event id", id)?;
                validate_error_code(&error.code)?;
                validate_required_message(&error.message)?;
                if let Some(details) = &error.details {
                    reject_secret_parameter_names(details).map_err(|_| {
                        BridgeError::worker("worker error details contained a secret field")
                    })?;
                }
                Ok(())
            }
            Self::Log { id, message, .. } => {
                if let Some(id) = id {
                    validate_event_identifier("event id", id)?;
                }
                validate_message(message)
            }
        }
    }
}

fn validate_event_identifier(label: &str, value: &str) -> BridgeResult<()> {
    if value == "worker-lifecycle" {
        Ok(())
    } else {
        validate_app_generated_identifier(label, value)
    }
}

fn validate_optional_message(message: Option<&str>) -> BridgeResult<()> {
    if let Some(message) = message {
        validate_message(message)?;
    }
    Ok(())
}

fn validate_message(message: &str) -> BridgeResult<()> {
    if message.len() > 16 * 1024 || message.contains('\0') {
        return Err(BridgeError::worker("worker emitted an invalid message"));
    }
    Ok(())
}

fn validate_required_message(message: &str) -> BridgeResult<()> {
    if message.is_empty() {
        return Err(BridgeError::worker("worker emitted an empty error message"));
    }
    validate_message(message)
}

fn validate_error_code(code: &str) -> BridgeResult<()> {
    if code.is_empty()
        || code.len() > 128
        || !code.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'_' | b'-')
        })
    {
        return Err(BridgeError::worker("worker emitted an invalid error code"));
    }
    Ok(())
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LogLevel {
    Debug,
    Info,
    Warning,
    Warn,
    Error,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerErrorBody {
    pub code: String,
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub details: Option<Value>,
    pub retryable: bool,
}

pub fn parse_worker_event(bytes: &[u8]) -> BridgeResult<WorkerEvent> {
    if bytes.is_empty() {
        return Err(BridgeError::worker("worker emitted an empty stdout line"));
    }
    if bytes.len() > MAX_EVENT_BYTES {
        return Err(BridgeError::worker(format!(
            "worker event exceeds the {MAX_EVENT_BYTES}-byte limit"
        )));
    }

    let event: WorkerEvent = serde_json::from_slice(bytes)
        .map_err(|_| BridgeError::worker("worker stdout was not a valid protocol event"))?;
    event.validate()?;
    Ok(event)
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEST_UUID: &str = "00000000-0000-4000-8000-000000000001";

    #[test]
    fn rejects_unknown_worker_methods() {
        let parsed = serde_json::from_str::<WorkerRequest>(
            r#"{"id":"ping-00000000-0000-4000-8000-000000000001","method":"run_shell","params":{}}"#,
        );
        assert!(parsed.is_err());
    }

    #[test]
    fn parses_progress_event() {
        let event = parse_worker_event(
            br#"{"type":"progress","id":"preview-00000000-0000-4000-8000-000000000001","job_id":"preview-00000000-0000-4000-8000-000000000001","stage":"render","completed":5,"total":10}"#,
        )
        .expect("valid event");

        assert!(matches!(
            event,
            WorkerEvent::Progress {
                completed: 5,
                total: 10,
                ..
            }
        ));
    }

    #[test]
    fn rejects_impossible_progress() {
        let event = parse_worker_event(
            br#"{"type":"progress","id":"preview-00000000-0000-4000-8000-000000000001","job_id":"preview-00000000-0000-4000-8000-000000000001","stage":"render","completed":11,"total":10}"#,
        );
        assert!(event.is_err());
    }

    #[test]
    fn request_ids_may_not_contain_newlines() {
        assert!(validate_identifier("id", "job\nsecond-line").is_err());
    }

    #[test]
    fn request_ids_require_matching_operation_prefix_and_uuid_v4() {
        let id = format!("preview-{TEST_UUID}");
        validate_request_identifier(WorkerMethod::RenderPreview, &id).expect("valid app id");
        assert!(validate_request_identifier(WorkerMethod::RenderFinal, &id).is_err());
        assert!(
            validate_request_identifier(WorkerMethod::RenderPreview, "sk-proj-secret").is_err()
        );
    }

    #[test]
    fn worker_params_reject_secret_names_and_unknown_fields() {
        for payload in [
            r#"{"id":"llm-test-00000000-0000-4000-8000-000000000001","method":"test_llm","params":{"apiKey":"not-allowed"}}"#,
            r#"{"id":"llm-test-00000000-0000-4000-8000-000000000001","method":"test_llm","params":{"openaiApiKey":"not-allowed"}}"#,
            r#"{"id":"llm-test-00000000-0000-4000-8000-000000000001","method":"test_llm","params":{"accessToken":"not-allowed"}}"#,
            r#"{"id":"llm-test-00000000-0000-4000-8000-000000000001","method":"test_llm","params":{"Authorization":"not-allowed"}}"#,
            r#"{"id":"ping-00000000-0000-4000-8000-000000000001","method":"ping","params":{"foo":"not-allowed"}}"#,
        ] {
            let request: WorkerRequest = serde_json::from_str(payload).expect("typed request");
            assert!(
                request.validate().is_err(),
                "payload unexpectedly passed: {payload}"
            );
        }
    }

    #[test]
    fn create_project_accepts_an_optional_bounded_name() {
        let request: WorkerRequest = serde_json::from_str(
            r#"{"id":"create-project-00000000-0000-4000-8000-000000000001","method":"create_project","params":{"input_path":"C:\\video.mp4","project_dir":"C:\\Projects\\Demo","name":"Cinema Déjà Vu"}}"#,
        )
        .expect("typed request");
        request.validate().expect("valid named project request");

        let mut boundary_params = Map::new();
        boundary_params.insert(
            "input_path".to_owned(),
            Value::String("C:\\video.mp4".to_owned()),
        );
        boundary_params.insert(
            "project_dir".to_owned(),
            Value::String("C:\\Projects\\Demo".to_owned()),
        );
        boundary_params.insert("name".to_owned(), Value::String("界".repeat(160)));
        WorkerRequest {
            id: format!("create-project-{TEST_UUID}"),
            method: WorkerMethod::CreateProject,
            params: boundary_params,
        }
        .validate()
        .expect("160-character multibyte project name");
    }

    #[test]
    fn create_project_rejects_invalid_names_and_unknown_parameters() {
        for name in [
            Value::String(String::new()),
            Value::String("   ".to_owned()),
            Value::String("x".repeat(161)),
            Value::String("two\nlines".to_owned()),
            Value::String("control\u{0085}name".to_owned()),
            Value::Number(42.into()),
        ] {
            let mut params = Map::new();
            params.insert(
                "input_path".to_owned(),
                Value::String("C:\\video.mp4".to_owned()),
            );
            params.insert(
                "project_dir".to_owned(),
                Value::String("C:\\Projects\\Demo".to_owned()),
            );
            params.insert("name".to_owned(), name);
            let request = WorkerRequest {
                id: format!("create-project-{TEST_UUID}"),
                method: WorkerMethod::CreateProject,
                params,
            };
            assert!(request.validate().is_err());
        }

        let request: WorkerRequest = serde_json::from_str(
            r#"{"id":"create-project-00000000-0000-4000-8000-000000000001","method":"create_project","params":{"input_path":"C:\\video.mp4","project_dir":"C:\\Projects\\Demo","name":"Feature","display_name":"not allowed"}}"#,
        )
        .expect("typed request");
        assert!(request.validate().is_err());
    }

    #[test]
    fn recommend_params_are_exact_and_numeric() {
        let valid: WorkerRequest = serde_json::from_str(
            r#"{"id":"recommend-00000000-0000-4000-8000-000000000001","method":"recommend_preset","params":{"features":{"shot_id":1,"duration_seconds":3.5,"motion_score":0.2,"speech_ratio":0.7,"depth_spread":0.3,"foreground_ratio":0.4,"brightness":0.5,"cut_frequency_context":0.1,"camera_movement":"static","depth_reliability":0.9}}}"#,
        )
        .expect("typed request");
        valid.validate().expect("canonical feature request");

        let nested_secret: WorkerRequest = serde_json::from_str(
            r#"{"id":"recommend-00000000-0000-4000-8000-000000000001","method":"recommend_preset","params":{"features":{"shot_id":1,"duration_seconds":3.5,"motion_score":0.2,"speech_ratio":0.7,"depth_spread":0.3,"foreground_ratio":0.4,"brightness":0.5,"cut_frequency_context":0.1,"authorization":"secret"}}}"#,
        )
        .expect("typed request");
        assert!(nested_secret.validate().is_err());
    }

    #[test]
    fn accepts_still_preview_requests_with_a_frame_and_colour_matrix() {
        let request: WorkerRequest = serde_json::from_str(
            r#"{"id":"preview-frame-00000000-0000-4000-8000-000000000001","method":"render_preview_frame","params":{"project_dir":"C:\\Projects\\Demo","shot_id":2,"frame_offset":0,"anaglyph_mode":"basic","swap_eyes":true,"depth_backend":"monocular-cues","device":"auto","allow_fallback":true}}"#,
        )
        .expect("typed request");
        request.validate().expect("canonical still preview request");
    }

    #[test]
    fn rejects_still_previews_with_an_unusable_frame_or_mismatched_prefix() {
        for payload in [
            // A negative frame cannot index a shot.
            r#"{"id":"preview-frame-00000000-0000-4000-8000-000000000001","method":"render_preview_frame","params":{"project_dir":"C:\\Projects\\Demo","shot_id":1,"frame_offset":-1}}"#,
            // Neither can a fractional or non-numeric one.
            r#"{"id":"preview-frame-00000000-0000-4000-8000-000000000001","method":"render_preview_frame","params":{"project_dir":"C:\\Projects\\Demo","shot_id":1,"frame_offset":"4"}}"#,
            // The still and video preview operations keep separate id prefixes.
            r#"{"id":"preview-00000000-0000-4000-8000-000000000001","method":"render_preview_frame","params":{"project_dir":"C:\\Projects\\Demo","shot_id":1}}"#,
            // frame_offset belongs to the still operation only.
            r#"{"id":"preview-00000000-0000-4000-8000-000000000001","method":"render_preview","params":{"project_dir":"C:\\Projects\\Demo","shot_id":1,"frame_offset":2}}"#,
        ] {
            let request: WorkerRequest = serde_json::from_str(payload).expect("typed request");
            assert!(request.validate().is_err());
        }
    }

    #[test]
    fn accepts_exact_shot_override_contract() {
        let request: WorkerRequest = serde_json::from_str(
            r#"{"id":"save-script-00000000-0000-4000-8000-000000000001","method":"apply_shot_overrides","params":{"project_dir":"C:\\Projects\\Demo","expected_revision":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","overrides":[{"shot_id":1,"preset":"neutral","parameters":{"depth_strength":0.5,"convergence_depth_percentile":0.5,"max_background_disparity_norm":0.01,"max_popout_disparity_norm":0.004,"temporal_smoothing":0.8,"transition_frames":8,"edge_protection":true}}]}}"#,
        )
        .expect("typed request");
        request.validate().expect("canonical override request");
    }

    #[test]
    fn preview_analysis_accepts_fallback_but_final_render_rejects_it() {
        for payload in [
            r#"{"id":"extract-features-00000000-0000-4000-8000-000000000001","method":"extract_features","params":{"project_dir":"C:\\Projects\\Demo","allow_fallback":true}}"#,
            r#"{"id":"direct-00000000-0000-4000-8000-000000000001","method":"direct","params":{"project_dir":"C:\\Projects\\Demo","allow_fallback":true}}"#,
            r#"{"id":"preview-00000000-0000-4000-8000-000000000001","method":"render_preview","params":{"project_dir":"C:\\Projects\\Demo","shot_id":1,"allow_fallback":true}}"#,
        ] {
            let request: WorkerRequest = serde_json::from_str(payload).expect("typed request");
            request
                .validate()
                .expect("preview analysis should permit explicit fallback");
        }

        let final_request: WorkerRequest = serde_json::from_str(
            r#"{"id":"export-00000000-0000-4000-8000-000000000001","method":"render_final","params":{"project_dir":"C:\\Projects\\Demo","allow_fallback":true}}"#,
        )
        .expect("typed request");
        assert!(final_request.validate().is_err());
    }

    #[test]
    fn analyze_draft_request_contract_is_named_and_strict() {
        let request: WorkerRequest = serde_json::from_str(
            r#"{"id":"analyze-draft-00000000-0000-4000-8000-000000000001","method":"analyze_draft","params":{"project_dir":"C:\\Projects\\Demo","resume":true,"force_stages":["analyze_draft","estimate_depth","extract_features","direct"],"depth_backend":"video-depth-anything-small","device":"auto","profile":"representative_frames","allow_fallback":true}}"#,
        )
        .expect("typed analyze draft request");
        request.validate().expect("valid analyze draft request");
        assert_eq!(request.method, WorkerMethod::AnalyzeDraft);
        assert_eq!(
            serde_json::to_value(&request).expect("serialized request")["method"],
            "analyze_draft"
        );
        let forwarded = serde_json::to_value(&request).expect("forwarded request JSON");
        assert_eq!(forwarded["params"]["profile"], "representative_frames");
        assert_eq!(forwarded["params"]["allow_fallback"], true);

        let cancellation: WorkerRequest = serde_json::from_str(
            r#"{"id":"cancel-00000000-0000-4000-8000-000000000001","method":"cancel_job","params":{"job_id":"analyze-draft-00000000-0000-4000-8000-000000000001"}}"#,
        )
        .expect("typed cancellation request");
        cancellation
            .validate()
            .expect("analyze draft is a cancellable background job");

        let minimal: WorkerRequest = serde_json::from_str(
            r#"{"id":"analyze-draft-00000000-0000-4000-8000-000000000001","method":"analyze_draft","params":{"project_dir":"C:\\Projects\\Demo"}}"#,
        )
        .expect("typed minimal request");
        minimal.validate().expect("minimal draft request");

        for payload in [
            r#"{"id":"analyze-draft-00000000-0000-4000-8000-000000000001","method":"analyze_draft","params":{}}"#,
            r#"{"id":"analyze-draft-00000000-0000-4000-8000-000000000001","method":"analyze_draft","params":{"project_dir":"C:\\Projects\\Demo","profile":"all_frames"}}"#,
            r#"{"id":"analyze-draft-00000000-0000-4000-8000-000000000001","method":"analyze_draft","params":{"project_dir":"C:\\Projects\\Demo","frames_per_shot":3}}"#,
            r#"{"id":"estimate-depth-00000000-0000-4000-8000-000000000001","method":"analyze_draft","params":{"project_dir":"C:\\Projects\\Demo"}}"#,
        ] {
            let invalid: WorkerRequest =
                serde_json::from_str(payload).expect("typed invalid request");
            assert!(
                invalid.validate().is_err(),
                "payload unexpectedly passed: {payload}"
            );
        }
    }

    fn valid_analyze_draft_result() -> Value {
        serde_json::json!({
            "analysis_tier": "sampled",
            "profile": "representative_frames",
            "features": {"schema_version": "1.0", "shots": []},
            "script": {"schema_version": "1.0", "shots": []},
            "revision": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "coverage": {
                "shot_ids": [1, 2],
                "sampled_frames": 8,
                "total_frames": 160,
                "per_shot": [
                    {"shot_id": 1, "sampled_frames": 5, "total_frames": 100},
                    {"shot_id": 2, "sampled_frames": 3, "total_frames": 60}
                ]
            }
        })
    }

    #[test]
    fn analyze_draft_result_requires_sampled_profile_revision_and_exact_coverage() {
        let id = format!("analyze-draft-{TEST_UUID}");
        WorkerEvent::Result {
            id: id.clone(),
            result: valid_analyze_draft_result(),
        }
        .validate()
        .expect("valid sampled draft result");

        let mut invalid_results = Vec::new();
        let mut wrong_tier = valid_analyze_draft_result();
        wrong_tier["analysis_tier"] = Value::String("production".to_owned());
        invalid_results.push(wrong_tier);
        let mut wrong_profile = valid_analyze_draft_result();
        wrong_profile["profile"] = Value::String("dense_frames".to_owned());
        invalid_results.push(wrong_profile);
        let mut invalid_revision = valid_analyze_draft_result();
        invalid_revision["revision"] = Value::String("A".repeat(64));
        invalid_results.push(invalid_revision);
        let mut missing_per_shot = valid_analyze_draft_result();
        missing_per_shot["coverage"]
            .as_object_mut()
            .expect("coverage object")
            .remove("per_shot");
        invalid_results.push(missing_per_shot);
        let mut mismatched_aggregate = valid_analyze_draft_result();
        mismatched_aggregate["coverage"]["sampled_frames"] = Value::from(9);
        invalid_results.push(mismatched_aggregate);
        let mut numeric_sampling_knob = valid_analyze_draft_result();
        numeric_sampling_knob
            .as_object_mut()
            .expect("result object")
            .insert("frames_per_shot".to_owned(), Value::from(3));
        invalid_results.push(numeric_sampling_knob);

        for result in invalid_results {
            assert!(WorkerEvent::Result {
                id: id.clone(),
                result,
            }
            .validate()
            .is_err());
        }
    }

    #[test]
    fn published_schema_contains_the_analyze_draft_boundary() {
        let schema: Value =
            serde_json::from_str(include_str!("../../contracts/worker-protocol.schema.json"))
                .expect("protocol schema JSON");
        let methods = schema["$defs"]["method"]["enum"]
            .as_array()
            .expect("method vocabulary");
        assert!(methods.iter().any(|method| method == "analyze_draft"));
        let params = &schema["$defs"]["analyzeDraftParams"];
        assert_eq!(params["additionalProperties"], false);
        assert_eq!(params["required"], serde_json::json!(["project_dir"]));
        assert_eq!(
            params["properties"]["profile"]["$ref"],
            "#/$defs/draftProfile"
        );
        assert_eq!(
            schema["$defs"]["draftProfile"]["const"],
            "representative_frames"
        );
        let coverage_required = schema["$defs"]["draftCoverage"]["required"]
            .as_array()
            .expect("coverage required keys");
        assert!(coverage_required.iter().any(|key| key == "per_shot"));
    }

    #[test]
    fn required_params_and_inspect_alternatives_are_enforced() {
        for payload in [
            r#"{"id":"create-project-00000000-0000-4000-8000-000000000001","method":"create_project","params":{"project_dir":"C:\\Projects\\Demo"}}"#,
            r#"{"id":"preview-00000000-0000-4000-8000-000000000001","method":"render_preview","params":{"project_dir":"C:\\Projects\\Demo"}}"#,
            r#"{"id":"inspect-00000000-0000-4000-8000-000000000001","method":"inspect","params":{}}"#,
            r#"{"id":"inspect-00000000-0000-4000-8000-000000000001","method":"inspect","params":{"input_path":"C:\\video.mp4","project_dir":"C:\\Projects\\Demo"}}"#,
        ] {
            let request: WorkerRequest = serde_json::from_str(payload).expect("typed request");
            assert!(
                request.validate().is_err(),
                "payload unexpectedly passed: {payload}"
            );
        }
    }

    #[test]
    fn webview_cannot_select_config_or_unreviewed_model() {
        for payload in [
            r#"{"id":"llm-test-00000000-0000-4000-8000-000000000001","method":"test_llm","params":{"model":"sk-proj-secret"}}"#,
            r#"{"id":"inspect-00000000-0000-4000-8000-000000000001","method":"inspect","params":{"input_path":"C:\\video.mp4","config_path":"C:\\evil.json"}}"#,
        ] {
            let request: WorkerRequest = serde_json::from_str(payload).expect("typed request");
            assert!(
                request.validate().is_err(),
                "payload unexpectedly passed: {payload}"
            );
        }

        let reviewed: WorkerRequest = serde_json::from_str(
            r#"{"id":"llm-test-00000000-0000-4000-8000-000000000001","method":"test_llm","params":{"model":"gpt-5.6-terra"}}"#,
        )
        .expect("typed request");
        reviewed.validate().expect("reviewed release model");
    }

    #[test]
    fn worker_events_are_exact_and_errors_require_retryable() {
        for payload in [
            br#"{"type":"result","id":"preview-00000000-0000-4000-8000-000000000001","result":{},"unexpected":true}"#.as_slice(),
            br#"{"type":"error","id":"preview-00000000-0000-4000-8000-000000000001","error":{"code":"render_failed","message":"failed"}}"#.as_slice(),
            br#"{"type":"error","id":"preview-00000000-0000-4000-8000-000000000001","error":{"code":"render_failed","message":"","retryable":false}}"#.as_slice(),
        ] {
            assert!(parse_worker_event(payload).is_err());
        }
    }
}
