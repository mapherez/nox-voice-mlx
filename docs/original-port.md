# Port history

This project retains the original PersonaPlex MLX inference work under the
repository's [MIT license](../LICENSE). Its model and inference sources are now
under `mac/engine/`; NVIDIA reference code is the pinned submodule under
`vendor/personaplex/`.

Older revisions used a single server and a different directory layout. Their
installation commands, dependency versions and benchmark claims are historical
and should not be used for the current application. The previous README is
available in Git history rather than duplicated as another installation guide.

The inherited full-model comparison diagnostic is retained at
`tests/mac/test_pytorch_mlx_comparison.py`. It is a manual diagnostic requiring
model files and additional reference dependencies, and is excluded from the
normal automated suite.

For current use, start with [Installation](getting-started.md). For component
licenses and upstream references, see [third-party notices](notices.md).
