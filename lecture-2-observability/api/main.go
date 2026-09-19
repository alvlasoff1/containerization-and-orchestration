// api is the "thing under test" for lab 2: metrics, logs and traces are all
// wired into it so there is something real to look at in Grafana/Jaeger.
//
//	GET /health  -> "ok"                          (liveness)
//	GET /fail    -> 500                           (error-rate signal)
//	GET /slow    -> sleeps 1-3s in a nested span  (latency signal)
//	GET /load    -> bursts requests at itself     (RPS signal)
//	GET /metrics -> Prometheus exposition
package main

import (
	"context"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.24.0"
	"go.opentelemetry.io/otel/trace"
)

var tracer = otel.Tracer("api")
var port = "8080"

var (
	reqCounter = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "http_requests_total",
		Help: "Total HTTP requests, by path and status",
	}, []string{"path", "status"})

	errCounter = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "http_errors_total",
		Help: "Total HTTP 5xx responses, by path",
	}, []string{"path"})

	reqDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_request_duration_seconds",
		Help:    "HTTP request duration in seconds, by path",
		Buckets: prometheus.DefBuckets,
	}, []string{"path"})
)

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))

	shutdown := initTracing()
	defer shutdown(context.Background())

	if v := os.Getenv("PORT"); v != "" {
		port = v
	}

	mux := http.NewServeMux()
	mux.Handle("/health", instrument("/health", http.HandlerFunc(health)))
	mux.Handle("/fail", instrument("/fail", http.HandlerFunc(fail)))
	mux.Handle("/slow", instrument("/slow", http.HandlerFunc(slow)))
	mux.Handle("/load", instrument("/load", http.HandlerFunc(load)))
	mux.Handle("/metrics", promhttp.Handler())

	// otelhttp wraps the whole mux, so every request gets a root span
	// automatically without each handler having to start one itself.
	handler := otelhttp.NewHandler(mux, "api")

	slog.Info("api listening", "port", port)
	if err := http.ListenAndServe(":"+port, handler); err != nil {
		slog.Error("server stopped", "err", err)
		os.Exit(1)
	}
}

func initTracing() func(context.Context) error {
	ctx := context.Background()

	exp, err := otlptracegrpc.New(ctx, otlptracegrpc.WithInsecure())
	if err != nil {
		slog.Error("otlp exporter init failed, traces disabled", "err", err)
		return func(context.Context) error { return nil }
	}

	res, _ := resource.New(ctx, resource.WithAttributes(
		semconv.ServiceName("api"),
	))

	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exp),
		sdktrace.WithResource(res),
	)
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	return tp.Shutdown
}

// instrument records Prometheus metrics and a structured log line for every
// request, and pulls the trace_id out of the span otelhttp already started
// so logs and traces can be cross-referenced.
func instrument(path string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		sw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(sw, r)
		dur := time.Since(start)

		reqCounter.WithLabelValues(path, strconv.Itoa(sw.status)).Inc()
		reqDuration.WithLabelValues(path).Observe(dur.Seconds())
		if sw.status >= 500 {
			errCounter.WithLabelValues(path).Inc()
		}

		traceID := trace.SpanContextFromContext(r.Context()).TraceID().String()
		slog.Info("request",
			"path", path,
			"status", sw.status,
			"duration_ms", dur.Milliseconds(),
			"trace_id", traceID,
		)
	})
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

func health(w http.ResponseWriter, _ *http.Request) {
	fmt.Fprintln(w, "ok")
}

func fail(w http.ResponseWriter, r *http.Request) {
	span := trace.SpanFromContext(r.Context())
	span.SetStatus(codes.Error, "simulated failure")
	span.SetAttributes(attribute.Bool("error", true))
	http.Error(w, "simulated failure\n", http.StatusInternalServerError)
}

// slow wraps its sleep in a nested "slow-op" span so the Jaeger waterfall
// shows exactly where the time went, not just that the request was slow.
func slow(w http.ResponseWriter, r *http.Request) {
	_, span := tracer.Start(r.Context(), "slow-op")
	defer span.End()

	d := time.Duration(1000+rand.Intn(2000)) * time.Millisecond
	span.SetAttributes(attribute.Int64("sleep_ms", d.Milliseconds()))
	time.Sleep(d)

	fmt.Fprintf(w, "slept %s\n", d)
}

// load fires a burst of concurrent requests at /health to spike RPS on the
// dashboards without needing an external load generator.
func load(w http.ResponseWriter, r *http.Request) {
	const n = 20
	client := &http.Client{Timeout: 5 * time.Second}
	base := "http://127.0.0.1:" + port

	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			resp, err := client.Get(base + "/health")
			if err == nil {
				resp.Body.Close()
			}
		}()
	}
	wg.Wait()

	fmt.Fprintf(w, "fired %d requests\n", n)
}
