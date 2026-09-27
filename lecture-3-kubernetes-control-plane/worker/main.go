// worker is the background half of the "shop" running example for lab 3.
// It polls postgres for orders in status 'new' and marks them 'processed'.
// A tiny HTTP server exposes /health (probe target) and /metrics.
//
// Postgres connection comes from DATABASE_URL, e.g.
//
//	postgres://shop:shop@postgres:5432/shop?sslmode=disable
package main

import (
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"time"

	_ "github.com/lib/pq" // database/sql driver
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	port         = getenv("PORT", "8080")
	healthFail   = os.Getenv("HEALTH_FAIL") == "true"
	pollInterval = getduration("POLL_INTERVAL", 2*time.Second)
)

var ordersProcessed = promauto.NewCounter(prometheus.CounterOpts{
	Name: "shop_orders_processed_total",
	Help: "Total orders marked processed by the worker",
})

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))

	db := openDB()
	defer db.Close()

	// HTTP server just for probes and metrics — the real work is the loop.
	// It starts first so /health is up even before postgres exists; the poll
	// loop below simply logs and retries until the database is reachable.
	go serveHTTP()

	slog.Info("worker started", "poll_interval", pollInterval.String())
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()
	for range ticker.C {
		n, err := processBatch(db)
		if err != nil {
			slog.Error("process batch", "err", err)
			continue
		}
		if n > 0 {
			slog.Info("processed orders", "count", n)
		}
	}
}

func serveHTTP() {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		if healthFail {
			http.Error(w, "unhealthy\n", http.StatusServiceUnavailable)
			return
		}
		fmt.Fprintln(w, "ok")
	})
	mux.Handle("/metrics", promhttp.Handler())

	slog.Info("worker http listening", "port", port)
	if err := http.ListenAndServe(":"+port, mux); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("http server stopped", "err", err)
		os.Exit(1)
	}
}

// processBatch claims a batch of new orders and marks them processed in one
// statement. FOR UPDATE SKIP LOCKED lets several worker replicas run at once
// without stepping on each other's rows.
func processBatch(db *sql.DB) (int64, error) {
	res, err := db.Exec(`
		UPDATE orders
		SET status = 'processed'
		WHERE id IN (
			SELECT id FROM orders
			WHERE status = 'new'
			ORDER BY id
			FOR UPDATE SKIP LOCKED
			LIMIT 10
		)`)
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	ordersProcessed.Add(float64(n))
	return n, nil
}

// openDB prepares the pool without dialing — the poll loop connects lazily on
// its first query and tolerates the database appearing later.
func openDB() *sql.DB {
	dsn := getenv("DATABASE_URL", "postgres://shop:shop@localhost:5432/shop?sslmode=disable")
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		slog.Error("open db", "err", err) // config error only (bad DSN)
		os.Exit(1)
	}
	db.SetMaxOpenConns(5)
	return db
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
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
