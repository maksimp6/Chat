/* fastcheck: exit 0 if build/native.apk is newer than every input, else exit 1.
 * Usage: fastcheck <project_dir>
 * Inputs: src/launcher.c, AndroidManifest.xml, tools/sign_v1.py */
#include <stdio.h>
#include <sys/stat.h>
#include <time.h>

static int mtime_of(const char* path, struct timespec* out) {
    struct stat st;
    if (stat(path, &st) != 0) return -1;
    *out = st.st_mtim;
    return 0;
}

static int newer(struct timespec a, struct timespec b) {
    return a.tv_sec > b.tv_sec || (a.tv_sec == b.tv_sec && a.tv_nsec > b.tv_nsec);
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const char* dir = argv[1];
    const char* inputs[] = {"src/launcher.c", "AndroidManifest.xml", "tools/sign_v1.py"};
    char path[1024];
    struct timespec out_t, in_t;

    snprintf(path, sizeof path, "%s/build/native.apk", dir);
    if (mtime_of(path, &out_t) != 0) return 1;

    for (size_t i = 0; i < sizeof inputs / sizeof inputs[0]; i++) {
        snprintf(path, sizeof path, "%s/%s", dir, inputs[i]);
        if (mtime_of(path, &in_t) != 0) return 1;
        if (newer(in_t, out_t)) return 1;
    }
    return 0;
}
