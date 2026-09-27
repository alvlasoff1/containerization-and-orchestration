// api is the HTTP front of the "shop" running example for lab 3.
//
//	GET  /health  -> "ok" (200), or 503 when HEALTH_FAIL=true  (probe target)
//	POST /order   -> writes an order to postgres
//	GET  /orders  -> reads and returns the list of orders
//	GET  /metrics -> Prometheus exposition (reused by Part 5)
//
// Postgres connection comes from DATABASE_URL, e.g.
//
//	postgres://shop:shop@postgres:5432/shop?sslmode=disable
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync/atomic"
	"syscall"
	"time"

	_ "github.com/lib/pq" // database/sql driver
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	port       = getenv("PORT", "8080")
	healthFail = os.Getenv("HEALTH_FAIL") == "true"
	// drainDelay: how long to keep serving after SIGTERM before shutting down,
	// so kube-proxy can remove this pod from the Service before the socket dies.
	drainDelay = getduration("DRAIN_DELAY", 5*time.Second)
)

// preStopRan records that the preStop hook already handled the drain delay, so
// the SIGTERM path doesn't sleep a second time.
var preStopRan atomic.Bool

var (
	ordersCreated = promauto.NewCounter(prometheus.CounterOpts{
		Name: "shop_orders_created_total",
		Help: "Total orders accepted by the api",
	})
	reqDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_request_duration_seconds",
		Help:    "HTTP request duration in seconds, by path and status",
		Buckets: prometheus.DefBuckets,
	}, []string{"path", "status"})
)

// Order is one row of the orders table.
type Order struct {
	ID        int64     `json:"id"`
	Item      string    `json:"item"`
	Quantity  int       `json:"quantity"`
	Status    string    `json:"status"`
	CreatedAt time.Time `json:"created_at"`
}

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))

	db := openDB()
	defer db.Close()
	// Schema creation and the first DB connection happen in the background so
	// the pod comes up and serves /health even before postgres exists (postgres
	// arrives in Part 3). Order handlers just return errors until it's ready.
	go ensureSchema(db)

	mux := http.NewServeMux()
	mux.Handle("/health", instrument("/health", http.HandlerFunc(health)))
	mux.Handle("/order", instrument("/order", orderHandler(db)))
	mux.Handle("/orders", instrument("/orders", ordersHandler(db)))
	mux.Handle("/metrics", promhttp.Handler())
	// preStop target: kubelet calls this (via lifecycle.preStop httpGet) BEFORE
	// SIGTERM. It marks the pod NotReady and blocks for drainDelay so the pod is
	// removed from the Service on every node before its socket closes. distroless
	// has no shell/sleep, so the delay lives here instead of an exec hook.
	mux.HandleFunc("/prestop", preStopHandler)

	srv := &http.Server{Addr: ":" + port, Handler: mux}

	// Trap SIGTERM (what kubelet sends) so we shut down gracefully instead of
	// the runtime killing us instantly.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	go func() {
		slog.Info("api listening", "port", port, "health_fail", healthFail)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("server stopped", "err", err)
			os.Exit(1)
		}
	}()

	<-ctx.Done()
	slog.Info("SIGTERM received")

	// Endpoint removal is already driven by the pod's deletion. If the preStop
	// hook covered the kube-proxy propagation window, don't sleep again;
	// otherwise (hook disabled) cover it here as a fallback.
	if !preStopRan.Load() {
		slog.Info("no preStop seen, draining inline", "delay", drainDelay.String())
		time.Sleep(drainDelay)
	}
	// Stop accepting and let in-flight requests finish.
	shutCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutCtx); err != nil {
		slog.Error("graceful shutdown failed", "err", err)
	}
	slog.Info("shutdown complete")
}

// preStopHandler is invoked by the kubelet's lifecycle.preStop httpGet before
// SIGTERM. It blocks for drainDelay, holding the pod alive and serving 200 while
// its endpoint removal reaches every kube-proxy — so no new connection is ever
// routed to a closed socket.
func preStopHandler(w http.ResponseWriter, _ *http.Request) {
	preStopRan.Store(true)
	slog.Info("preStop: holding before shutdown", "delay", drainDelay.String())
	time.Sleep(drainDelay)
	fmt.Fprintln(w, "draining")
}

// openDB prepares the pool. sql.Open doesn't dial — the first real connection
// happens lazily on use — so this never blocks pod startup.
func openDB() *sql.DB {
	dsn := getenv("DATABASE_URL", "postgres://shop:shop@localhost:5432/shop?sslmode=disable")
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		slog.Error("open db", "err", err) // config error only (bad DSN)
		os.Exit(1)
	}
	db.SetMaxOpenConns(10)
	return db
}

// ensureSchema keeps retrying the migration until postgres is reachable, so the
// service tolerates the database coming up after it does.
func ensureSchema(db *sql.DB) {
	for {
		if err := migrate(db); err != nil {
			slog.Warn("waiting for postgres / migrate", "err", err)
			time.Sleep(2 * time.Second)
			continue
		}
		slog.Info("schema ready")
		return
	}
}

func migrate(db *sql.DB) error {
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS orders (
			id         BIGSERIAL PRIMARY KEY,
			item       TEXT        NOT NULL,
			quantity   INT         NOT NULL DEFAULT 1,
			status     TEXT        NOT NULL DEFAULT 'new',
			created_at TIMESTAMPTZ NOT NULL DEFAULT now()
		)`)
	return err
}

// health is the probe target. Returns 503 only when HEALTH_FAIL=true (Part 2:
// stuck rollout). During shutdown the pod keeps answering 200 on purpose — it
// must stay serving while it drains, so a /health load test sees no failures.
func health(w http.ResponseWriter, _ *http.Request) {
	if healthFail {
		http.Error(w, "unhealthy\n", http.StatusServiceUnavailable)
		return
	}
	fmt.Fprintln(w, "ok")
}

// orderHandler writes a new order. Body: {"item":"book","quantity":2}
func orderHandler(db *sql.DB) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed\n", http.StatusMethodNotAllowed)
			return
		}
		var in struct {
			Item     string `json:"item"`
			Quantity int    `json:"quantity"`
		}
		if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
			http.Error(w, "bad json\n", http.StatusBadRequest)
			return
		}
		if in.Item == "" {
			http.Error(w, "item is required\n", http.StatusBadRequest)
			return
		}
		if in.Quantity <= 0 {
			in.Quantity = 1
		}

		var o Order
		err := db.QueryRowContext(r.Context(),
			`INSERT INTO orders (item, quantity) VALUES ($1, $2)
			 RETURNING id, item, quantity, status, created_at`,
			in.Item, in.Quantity,
		).Scan(&o.ID, &o.Item, &o.Quantity, &o.Status, &o.CreatedAt)
		if err != nil {
			slog.Error("insert order", "err", err)
			http.Error(w, "db error\n", http.StatusInternalServerError)
			return
		}
		ordersCreated.Inc()

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(o)
	})
}

// ordersHandler reads back all orders, newest first.
func ordersHandler(db *sql.DB) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rows, err := db.QueryContext(r.Context(),
			`SELECT id, item, quantity, status, created_at FROM orders ORDER BY id DESC`)
		if err != nil {
			slog.Error("select orders", "err", err)
			http.Error(w, "db error\n", http.StatusInternalServerError)
			return
		}
		defer rows.Close()

		orders := []Order{}
		for rows.Next() {
			var o Order
			if err := rows.Scan(&o.ID, &o.Item, &o.Quantity, &o.Status, &o.CreatedAt); err != nil {
				slog.Error("scan order", "err", err)
				http.Error(w, "db error\n", http.StatusInternalServerError)
				return
			}
			orders = append(orders, o)
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(orders)
	})
}

// instrument records request duration and a structured log line per request.
func instrument(path string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		sw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(sw, r)
		dur := time.Since(start)

		reqDuration.WithLabelValues(path, strconv.Itoa(sw.status)).Observe(dur.Seconds())
		slog.Info("request",
			"method", r.Method,
			"path", path,
			"status", sw.status,
			"duration_ms", dur.Milliseconds(),
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
