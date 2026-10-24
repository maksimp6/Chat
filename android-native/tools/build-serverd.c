/* build-serverd: резидентный dev-сервер сборки.
 * Следит за входными файлами (опрос каждые 300 мс), пересобирает APK в фоне,
 * по сокету отвечает на запросы: "status" и "build" (ждёт готовности и возвращает путь).
 * Запуск: ./build-serverd <project_dir> &  */
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <utime.h>

extern char** environ;

static const char* INPUTS[] = {"src/launcher.c", "AndroidManifest.xml", "tools/build-full.sh", "tools/sign_v1.py"};
static char dir[512], sock_path[512], apk_path[600];
static int last_ok = 0;
static long last_build_rc = -1;
static long built_count = 0;

/* метрики */
static long build_fails = 0;
static double last_build_ms = 0, total_build_ms = 0, max_build_ms = 0;
static long requests = 0;
static double last_req_ms = 0, total_req_ms = 0, max_req_ms = 0;
static double started_s = 0;

static long rss_kb(void) {
    FILE* f = fopen("/proc/self/status", "r");
    if (!f) return -1;
    char line[256];
    long kb = -1;
    while (fgets(line, sizeof line, f)) {
        if (sscanf(line, "VmRSS: %ld", &kb) == 1) break;
    }
    fclose(f);
    return kb;
}

static double now_s(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static int mtime_of(const char* p, struct timespec* t) {
    struct stat st;
    if (stat(p, &st) != 0) return -1;
    *t = st.st_mtim;
    return 0;
}

static int newer(struct timespec a, struct timespec b) {
    return a.tv_sec > b.tv_sec || (a.tv_sec == b.tv_sec && a.tv_nsec > b.tv_nsec);
}

/* 1 если есть вход новее APK или APK нет */
static int stale(void) {
    char p[700];
    struct timespec out_t, in_t;
    snprintf(p, sizeof p, "%s/build/native.apk", dir);
    if (mtime_of(p, &out_t) != 0) return 1;
    for (size_t i = 0; i < sizeof INPUTS / sizeof INPUTS[0]; i++) {
        snprintf(p, sizeof p, "%s/%s", dir, INPUTS[i]);
        if (mtime_of(p, &in_t) != 0) return 1;
        if (newer(in_t, out_t)) return 1;
    }
    return 0;
}

static void run_build(void) {
    char script[700];
    snprintf(script, sizeof script, "%s/tools/build-full.sh", dir);
    char* argv[] = {"bash", script, NULL};
    pid_t pid;
    if (posix_spawn(&pid, "/data/data/com.termux/files/usr/bin/bash", NULL, NULL, argv, environ) != 0) {
        last_ok = 0;
        last_build_rc = -2;
        return;
    }
    double t0 = now_s();
    int st = 0;
    waitpid(pid, &st, 0);
    double dt = (now_s() - t0) * 1000.0;
    last_build_ms = dt;
    total_build_ms += dt;
    if (dt > max_build_ms) max_build_ms = dt;
    last_build_rc = WIFEXITED(st) ? WEXITSTATUS(st) : -3;
    last_ok = (last_build_rc == 0);
    if (!last_ok) build_fails++;
    /* build-full.sh может не перезаписать APK (кэш), поэтому освежаем его mtime,
     * иначе сервер решит, что сборка всё ещё нужна, и будет крутиться впустую. */
    if (last_ok) utime(apk_path, NULL);
    built_count++;
}

static void reply(int fd, const char* text) {
    write(fd, text, strlen(text));
}

static void handle(int fd) {
    char buf[64] = {0};
    ssize_t n = read(fd, buf, sizeof buf - 1);
    if (n <= 0) return;
    if (strncmp(buf, "ping", 4) == 0) {
        reply(fd, "pong\n");
    } else if (strncmp(buf, "metrics", 7) == 0) {
        char msg[512];
        double avg_build = built_count ? total_build_ms / built_count : 0;
        double avg_req = requests ? total_req_ms / requests : 0;
        snprintf(msg, sizeof msg,
                 "uptime_s=%.1f\nbuilds=%ld fails=%ld last_build_ms=%.0f avg_build_ms=%.0f max_build_ms=%.0f\n"
                 "requests=%ld last_req_ms=%.1f avg_req_ms=%.1f max_req_ms=%.1f\nstale=%d\n",
                 now_s() - started_s, built_count, build_fails, last_build_ms, avg_build, max_build_ms,
                 requests, last_req_ms, avg_req, max_req_ms, stale());
        reply(fd, msg);
        snprintf(msg, sizeof msg, "rss_kb=%ld\n", rss_kb());
        reply(fd, msg);
        char tpath[700];
        snprintf(tpath, sizeof tpath, "%s/build/timings.txt", dir);
        FILE* tf = fopen(tpath, "r");
        if (tf) {
            reply(fd, "steps_last_build:\n");
            char tl[128];
            while (fgets(tl, sizeof tl, tf)) reply(fd, tl);
            fclose(tf);
        }
    } else if (strncmp(buf, "status", 6) == 0) {
        char msg[256];
        snprintf(msg, sizeof msg, "stale=%d last_ok=%d last_rc=%ld builds=%ld\n",
                 stale(), last_ok, last_build_rc, built_count);
        reply(fd, msg);
    } else if (strncmp(buf, "build", 5) == 0) {
        if (stale()) run_build();
        if (!stale()) {
            char msg[700];
            snprintf(msg, sizeof msg, "ok %s\n", apk_path);
            reply(fd, msg);
        } else {
            char msg[64];
            snprintf(msg, sizeof msg, "fail rc=%ld\n", last_build_rc);
            reply(fd, msg);
        }
    } else {
        reply(fd, "unknown\n");
    }
}

int main(int argc, char** argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <project_dir>\n", argv[0]);
        return 2;
    }
    snprintf(dir, sizeof dir, "%s", argv[1]);
    snprintf(apk_path, sizeof apk_path, "%s/build/native.apk", dir);
    const char* home = getenv("HOME");
    snprintf(sock_path, sizeof sock_path, "%s/.build-native.sock", home ? home : ".");

    signal(SIGPIPE, SIG_IGN);
    started_s = now_s();
    unlink(sock_path);
    int srv = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un addr = {0};
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof addr.sun_path, "%s", sock_path);
    if (bind(srv, (struct sockaddr*)&addr, sizeof addr) != 0 || listen(srv, 8) != 0) {
        perror("bind/listen");
        return 1;
    }
    fprintf(stderr, "build-serverd: listening on %s\n", sock_path);

    /* Первая сборка сразу, если APK устарел. */
    if (stale()) run_build();

    for (;;) {
        /* Фоновая пересборка при изменении входов. */
        if (stale()) run_build();

        /* Коротко ждём запросов (300 мс), потом снова проверяем файлы. */
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(srv, &rfds);
        struct timeval tv = {0, 300000};
        if (select(srv + 1, &rfds, NULL, NULL, &tv) > 0) {
            int c = accept(srv, NULL, NULL);
            if (c >= 0) {
                double r0 = now_s();
                handle(c);
                double r = (now_s() - r0) * 1000.0;
                requests++;
                last_req_ms = r;
                total_req_ms += r;
                if (r > max_req_ms) max_req_ms = r;
                close(c);
            }
        }
    }
}
