# Multi-stage build for the `api` service (Part 6).
#
# Stage 1 compiles a fully static binary; stage 2 is `scratch` — an empty image
# with nothing but our binary. No shell, no libc, no package manager: minimal
# size and minimal attack surface.

# ---- build stage ---------------------------------------------------------
FROM golang:1.26 AS build
WORKDIR /src

# Dependencies first so the layer is cached across code-only rebuilds. The
# service is stdlib-only, so this is just go.mod (no go.sum yet).
COPY go.mod ./
RUN go mod download

COPY . .

# CGO_ENABLED=0 -> no dynamic libc, a self-contained static binary that runs on
# `scratch`. -ldflags "-s -w" strips debug info to shrink the binary.
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /api .

# ---- runtime stage -------------------------------------------------------
FROM scratch

# api is PID 1 here; it handles SIGTERM itself (see main.go), so no init needed.
COPY --from=build /api /api

EXPOSE 8080
ENV PORT=8080

# Run as a non-root uid. scratch has no /etc/passwd, so a numeric uid is used.
USER 65534:65534

ENTRYPOINT ["/api"]
