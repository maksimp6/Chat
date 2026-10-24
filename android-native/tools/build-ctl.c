/* build-ctl: клиент build-serverd. Использование: build-ctl ping|status|build */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char** argv) {
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    if (argc != 2) {
        fprintf(stderr, "usage: %s ping|status|build\n", argv[0]);
        return 2;
    }
    const char* home = getenv("HOME");
    char sock_path[512];
    snprintf(sock_path, sizeof sock_path, "%s/.build-native.sock", home ? home : ".");

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un addr = {0};
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof addr.sun_path, "%s", sock_path);
    if (connect(fd, (struct sockaddr*)&addr, sizeof addr) != 0) {
        fprintf(stderr, "server not running (%s)\n", sock_path);
        return 1;
    }
    write(fd, argv[1], strlen(argv[1]));
    static char buf[8192];
    size_t total = 0;
    ssize_t n;
    while ((n = read(fd, buf + total, sizeof buf - 1 - total)) > 0) {
        total += (size_t)n;
        if (total >= sizeof buf - 1) break;
    }
    if (total == 0) return 1;
    buf[total] = '\0';
    fputs(buf, stdout);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    fprintf(stderr, "client_round_trip_ms=%.1f\n", (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6);
    return strncmp(buf, "fail", 4) == 0 ? 1 : 0;
}
