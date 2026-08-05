# kdb-x — KDB-X as a container, for any platform

**This directory ships no KX software.** It is a recipe. The build downloads
KDB-X from KX's own distribution portal, so you obtain it directly from KX
under your own acceptance of their terms — nothing here redistributes a kdb+
binary, which the licence does not permit.

The licence is not baked in either. Mount your own at runtime.

## Build

Single platform, straight into your local daemon:

```bash
docker build -t kdb-x:local kdb-x/
```

Real multi-arch (buildx cannot `--load` two platforms, hence `--push`):

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t ghcr.io/<you>/kdb-x:1.1 --push kdb-x/
docker buildx imagetools inspect ghcr.io/<you>/kdb-x:1.1   # verify BOTH are there
```

That last check matters. A single-arch tag still resolves on the platform it
happens to match, so a broken manifest looks healthy until an amd64 host
silently pulls AArch64 binaries — `docker pull` and `COPY` never execute
anything, so the failure surfaces only when something tries to spawn q.

The build asserts the architecture itself: it reads the ELF `e_machine` of the
downloaded `q` and fails if it does not match the platform being built.

## Platform names

KX names builds by OS *and* CPU, and the OS half is easy to miss:

| Target | KX prefix | Format |
|---|---|---|
| Linux x86-64 (most servers, NAS) | `l64` | ELF |
| Linux AArch64 (Docker on Apple Silicon) | `l64arm` | ELF |
| macOS Apple Silicon (native, not containers) | `m64` | Mach-O |

A `m64` build will not run in a Linux container at all. If you ran KX's
installer on a Mac it gave you `m64` — useful for running `q` in a terminal,
not for this.

## Run

```bash
docker run -d --name kdb \
  -v "$PWD/../kdb-license:/licence:ro" -e QLIC=/licence \
  -p 127.0.0.1:5000:5000 kdb-x:local
```

`KX_PORT` defaults to `127.0.0.1:5000` — loopback, deliberately. In a stack
where containers share a network namespace, `0.0.0.0` publishes an
unauthenticated q, and q IPC executes arbitrary q, to every peer on the
network. Bind `0.0.0.0` **only** for a standalone container whose exposure you
control with `-p 127.0.0.1:...` on the host side; a loopback bind inside the
container is unreachable through `-p`.

## The licence file

KX emails the licence as **base64**; `q` wants it **decoded**:

```bash
base64 -d < licence.b64 > kdb-license/kc.lic
```

If you ran KX's installer, `~/.kx/kc.lic` is already decoded and can be copied
straight across — it is platform-independent, unlike the binary.
