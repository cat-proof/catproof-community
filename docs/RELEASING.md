# Releasing Community Edition

1. Confirm that the maintainer has selected the license and committed its unmodified text. Do not call a source snapshot open source before those terms exist.
2. Run the source credential scan and review runtime files, third-party material, licenses and release notes. The automated scan is bounded and does not replace review.
3. Require successful Community checks for the exact source revision, including PostgreSQL tests and the HTTPS/restart smoke test. Keep macOS/Windows host validation separate from Linux and PowerShell syntax results.
4. Tag the reviewed revision with a version such as `v0.1.0`. The image workflow repeats validation before publishing Linux amd64/arm64 images with provenance and SBOM output.
5. Verify package visibility independently of repository visibility. Confirm that an unauthenticated client can pull the published version. Inspect the image digest and test the pulled image, not only the source build.
6. Export a source/installation bundle with `python3 scripts/package_release.py --version 0.1.0 --ref v0.1.0`. Attach it and its SHA256SUMS file to the GitHub release, together with validation results and known limits. Do not include `.env`, runtime volumes, credentials or Git history.
7. Update the website download links only after the repository, release and image are publicly accessible. Preserve the private hosted-service repository and its visibility.
