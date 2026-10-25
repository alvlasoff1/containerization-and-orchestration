FROM python:3.12-alpine
COPY ollama_exporter.py /ollama_exporter.py
USER 65534:65534
EXPOSE 9101
ENTRYPOINT ["python3", "-u", "/ollama_exporter.py"]
