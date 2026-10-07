// batch is the sacrificial background load for lab 4 — a stand-in for the
// "nightly analytics" that can be thrown away under pressure. It does nothing
// useful: it burns CPU in a busy loop and holds a configurable amount of
// resident memory, so the cluster can be pushed into scarcity on purpose.
//
// Everything is driven by env vars so the shop chart can turn the knobs from
// values.yaml (more replicas, more memory) without rebuilding the image:
//
//	BURN_CPUS   number of CPU-burning goroutines (default: all cores)
//	MEM_MB      resident memory to allocate and keep touched (default: 0)
//	MEM_TOUCH   how often to re-touch the pages so they stay resident
//	PORT        HTTP port for /health and /metrics (default: 8080)
//
// A tiny HTTP server exposes /health (probe target) and /metrics, so batch
// looks like every other pod to Kubernetes and the Prometheus stack.
package main

import (
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"runtime"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	port      = getenv("PORT", "8080")
	burnCPUs  = getint("BURN_CPUS", runtime.NumCPU())
	memMB     = getint("MEM_MB", 0)
	memTouch  = getduration("MEM_TOUCH", 5*time.Second)
	healthBad = os.Getenv("HEALTH_FAIL") == "true"
)

var (
	iterations = promauto.NewCounter(prometheus.CounterOpts{
		Name: "batch_iterations_total",
		Help: "Total busy-loop iterations across all CPU-burning goroutines",
	})
	allocatedBytes = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "batch_allocated_bytes",
		Help: "Resident memory batch deliberately holds",
	})
)

// pageSize is a safe stride for touching pages so the kernel keeps them in RSS
// instead of reclaiming untouched anonymous memory.
const pageSize = 4096

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))

	// Probes and metrics come up first, independent of the burning below.
	go serveHTTP()

	hold := holdMemory(memMB)
	slog.Info("batch started", "burn_cpus", burnCPUs, "mem_mb", memMB)

	for i := 0; i < burnCPUs; i++ {
		go burnCPU()
	}

	// Keep the held pages resident: untouched anonymous memory can be reclaimed,
	// which would hide the pressure we are trying to create.
	ticker := time.NewTicker(memTouch)
	defer ticker.Stop()
	for range ticker.C {
		touch(hold)
	}
}

// burnCPU spins forever doing arithmetic the compiler can't elide, pinning a
// core at 100%. The atomic counter doubles as proof of life in /metrics.
func burnCPU() {
	x := uint64(1)
	for {
		for n := 0; n < 1_000_000; n++ {
			x = x*1103515245 + 12345 // a cheap LCG, result is never used
		}
		iterations.Inc()
		if x == 0 { // unreachable, but stops the loop being optimized away
			slog.Info("impossible", "x", x)
		}
	}
}

// holdMemory allocates mb megabytes, faults every page in once, and returns the
// buffer so the GC can't collect it. A nil return means nothing was requested.
func holdMemory(mb int) []byte {
	if mb <= 0 {
		return nil
	}
	buf := make([]byte, mb*1024*1024)
	touch(buf)
	allocatedBytes.Set(float64(len(buf)))
	return buf
}

func touch(buf []byte) {
	for i := 0; i < len(buf); i += pageSize {
		buf[i] = byte(atomic.AddUint64(&tick, 1))
	}
}

var tick uint64

func serveHTTP() {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		if healthBad {
			http.Error(w, "unhealthy\n", http.StatusServiceUnavailable)
			return
		}
		fmt.Fprintln(w, "ok")
	})
	mux.Handle("/metrics", promhttp.Handler())

	slog.Info("batch http listening", "port", port)
	if err := http.ListenAndServe(":"+port, mux); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("http server stopped", "err", err)
		os.Exit(1)
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getint(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func getduration(key string, fallback time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return fallback
}
