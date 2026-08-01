# Reviewed media-tool staging

`scripts/package-worker.ps1` copies independently reviewed `ffmpeg.exe` and
`ffprobe.exe` files here only after their caller-supplied SHA-256 values match.
It also stages the distributor's license and corresponding-source offer. These
generated release inputs are intentionally ignored by Git.
