# openbb-docker

Self-hosted **OpenBB Platform** in Docker, behind a Tailscale sidecar — the
backend for the **Adventures in OpenBB** series. Each tagged release is the
companion code for one episode: check out the tag, follow that episode's
"For the tinkerers" section, and everything you need is here — and nothing
from later chapters is.

*The release map fills in here as episodes publish.*

**Status: scaffold — v1.0.0 in progress.**

All hostnames in this repo are placeholders (`<your-tailnet>.ts.net`);
credentials live in gitignored env files. CI runs `scripts/scrub-check.sh`
to keep it that way.
