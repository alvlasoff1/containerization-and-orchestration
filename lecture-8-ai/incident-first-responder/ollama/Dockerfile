# Ollama только с CPU-бэкендом: из официального релиза взяты bin/ollama и
# lib/ollama без библиотек CUDA/ROCm/Vulkan (72 MB вместо ~3 GB).
# Подготовка: curl -sL .../ollama-linux-amd64.tar.zst | zstd -dc |
#   tar x --exclude="lib/ollama/cuda*" --exclude="lib/ollama/*vulkan*"
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY ollama-dist/bin/ollama /usr/local/bin/ollama
COPY ollama-dist/lib/ollama /usr/local/lib/ollama
ENV OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_MODELS=/models
EXPOSE 11434
ENTRYPOINT ["/usr/local/bin/ollama"]
CMD ["serve"]
