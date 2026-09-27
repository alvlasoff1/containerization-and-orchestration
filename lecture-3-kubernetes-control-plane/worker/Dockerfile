FROM golang:1.25 AS build
WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /worker .

FROM gcr.io/distroless/static-debian12
COPY --from=build /worker /worker

EXPOSE 8080
ENV PORT=8080

USER 65534:65534
ENTRYPOINT ["/worker"]
