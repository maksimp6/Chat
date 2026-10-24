/* Точка входа сборки: если APK актуален, выходит сразу без bash; иначе запускает tools/build-full.sh. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static int mtime_of(const char* path, struct timespec* out) {
    struct stat st;
    if (stat(path, &st) != 0) return -1;
    *out = st.st_mtim;
    return 0;
}

static int newer(struct timespec a, struct timespec b) {
    return a.tv_sec > b.tv_sec || (a.tv_sec == b.tv_sec && a.tv_nsec > b.tv_nsec);
}

static int up_to_date(const char* dir) {
    const char* inputs[] = {"src/launcher.c", "AndroidManifest.xml", "tools/build-full.sh", "tools/sign_v1.py"};
    char path[1024];
    struct timespec out_t, in_t;
    snprintf(path, sizeof path, "%s/build/native.apk", dir);
    if (mtime_of(path, &out_t) != 0) return 0;
    for (size_t i = 0; i < sizeof inputs / sizeof inputs[0]; i++) {
        snprintf(path, sizeof path, "%s/%s", dir, inputs[i]);
        if (mtime_of(path, &in_t) != 0) return 0;
        if (newer(in_t, out_t)) return 0;
    }
    return 1;
}

int main(void) {
    char self[1024];
    char dir[1024];
    ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1);
    if (n <= 0) return 1;
    self[n] = '\0';
    char* slash = strrchr(self, '/');
    if (!slash) return 1;
    *slash = '\0';
    snprintf(dir, sizeof dir, "%s", self);

    if (up_to_date(dir)) {
        puts("up to date: native.apk reused");
        return 0;
    }
    char script[1100];
    snprintf(script, sizeof script, "%s/tools/build-full.sh", dir);
    execl("/data/data/com.termux/files/usr/bin/bash", "bash", script, (char*)NULL);
    return 1;
}
