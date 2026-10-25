FROM python:3.12-alpine
COPY gate.py /gate.py
USER 65534:65534
ENTRYPOINT ["python3", "-u", "/gate.py"]
