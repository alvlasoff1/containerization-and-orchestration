// api is a deliberately tiny HTTP service used as the "thing under test" for
// lab 1. It does three things, each meant to be poked from the outside so you
// can watch a kernel limit react:
//
//	GET /health      -> "ok"                     (liveness)
//	GET /eat?mb=N    -> allocate N MiB and hold   (trip a cgroup memory limit / OOM)
//	GET /burn        -> peg one CPU core forever  (trip a cgroup CPU quota / throttling)
//
// It is intentionally dependency-free (stdlib only) so it compiles to a single
// static binary you can run as a bare process under `unshare`, drop into a
// `scratch` image, and generally treat as a black box.
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"strconv"
	"sync"
	"syscall"
	"time"
)

// held keeps allocated buffers reachable so the garbage collector can never
// reclaim them — that is the whole point of /eat: the memory must stay resident
// until the process dies (or the kernel kills it).
var (
	mu   sync.Mutex
	held [][]byte
)

func main() {
	addr := ":8080"
	if v := os.Getenv("PORT"); v != "" {
		addr = ":" + v
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", health)
	mux.HandleFunc("/eat", eat)
	mux.HandleFunc("/burn", burn)

	srv := &http.Server{Addr: addr, Handler: mux}

	// Handle SIGTERM/SIGINT ourselves. Inside a container this process is PID 1,
	// and PID 1 gets no default signal handlers — without this the container
	// would ignore `docker stop` and only die on the kill-after-timeout. This is
	// the "PID 1 trap" from the lecture, handled the app-side way.
	go func() {
		stop := make(chan os.Signal, 1)
		signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
		sig := <-stop
		log.Printf("got %s, shutting down", sig)
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(ctx)
	}()

	log.Printf("api listening on %s (pid %d)", addr, os.Getpid())
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}

// health is the liveness endpoint: always cheap, always "ok".
func health(w http.ResponseWriter, _ *http.Request) {
	fmt.Fprintln(w, "ok")
}

// eat allocates N MiB and holds it forever. It writes one byte per 4 KiB page so
// the pages are actually faulted in and counted as RSS — otherwise Linux would
// hand back lazily-zeroed pages that never touch the cgroup's memory counter.
func eat(w http.ResponseWriter, r *http.Request) {
	mb, err := strconv.Atoi(r.URL.Query().Get("mb"))
	if err != nil || mb <= 0 {
		http.Error(w, "pass ?mb=N with N > 0\n", http.StatusBadRequest)
		return
	}

	const mib = 1 << 20
	buf := make([]byte, mb*mib)
	for i := 0; i < len(buf); i += 4096 {
		buf[i] = 1 // fault in the page so it counts against the memory limit
	}

	mu.Lock()
	held = append(held, buf)
	total := 0
	for _, b := range held {
		total += len(b)
	}
	mu.Unlock()

	fmt.Fprintf(w, "allocated %d MiB, now holding %d MiB\n", mb, total/mib)
}

// burn pins exactly one CPU core in a busy loop until the process ends. One
// goroutine locked to one OS thread is enough to saturate a single core, which
// is what you want to see throttled by a CPU quota.
func burn(w http.ResponseWriter, _ *http.Request) {
	go func() {
		runtime.LockOSThread()
		x := 0
		for {
			x++
		}
	}()
	fmt.Fprintln(w, "burning one CPU core")
}
