# Privacy

Parallax Forge release one is designed for offline processing by default. The selected source video remains at its original location; the project stores its local path plus derived normalized media, audio intermediates, depth maps, stereo scripts, renders, and QC reports inside the folder chosen by the user.

The application does not include accounts, cloud media uploads, advertising, or analytics. If the user explicitly enables the optional LLM Assistant, the app sends only compact shot statistics and a bounded instruction to the configured provider; it does not send video frames, thumbnails, depth maps, audio, subtitles, filenames, or project paths. The settings screen must disclose the provider before a request is made. The optional depth checkpoint is staged separately during a controlled release setup; the app never downloads it at runtime, and production installers disclose its source and verify its hash.

The LLM API key is stored in the operating system credential store on certified platforms or supplied through a process environment for development. It is never written to the project, application settings, logs, crash reports, or browser storage. Deleting the app-managed credential disables remote recommendations unless a development environment key is still active; deterministic stereo conversion is unaffected.

Logs contain operational metadata and errors. They should not contain decoded frames, audio content, filenames in remote telemetry, or model prompts. The provider still receives normal API account and network metadata under its own terms. Users can delete a project by deleting its project folder; the app should provide a reviewed, recoverable delete workflow before public launch.

This document is product behavior, not a substitute for a jurisdiction-specific privacy policy. Add company identity, contact details, retention terms, and applicable legal rights before sale.
