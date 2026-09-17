FROM golang:1.26 AS build
WORKDIR /src

COPY . .

RUN go mod download

RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /api .

# FROM scratch

# COPY --from=build /api /api

EXPOSE 8080
ENV PORT=8080

# USER 65534:65534

ENTRYPOINT ["/api"]
